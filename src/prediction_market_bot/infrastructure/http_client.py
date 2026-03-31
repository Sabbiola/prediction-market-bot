from __future__ import annotations

import json
import random
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, NoReturn
from urllib import error, parse, request


@dataclass(slots=True, frozen=True)
class HttpErrorMetadata:
    source: str
    method: str
    url: str
    attempt: int
    max_retries: int
    timeout_sec: float
    status_code: int | None
    retryable: bool
    classification: str
    reason_code: str
    error_type: str
    message: str
    timestamp: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "method": self.method,
            "url": self.url,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "timeout_sec": self.timeout_sec,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "classification": self.classification,
            "reason_code": self.reason_code,
            "error_type": self.error_type,
            "message": self.message,
            "timestamp": self.timestamp,
        }


class HttpClientError(RuntimeError):
    def __init__(self, metadata: HttpErrorMetadata) -> None:
        super().__init__(f"{metadata.source}_http_error: {metadata.error_type} ({metadata.message})")
        self.metadata = metadata


class RetryableHttpError(HttpClientError):
    """Error that is transient and may succeed on retry (5xx, timeout, network)."""

    pass


class PermanentHttpError(HttpClientError):
    """Error that will not succeed on retry (4xx client errors, malformed payload)."""

    pass


def _raise_http_error(metadata: HttpErrorMetadata) -> "NoReturn":
    """Raise the appropriate HttpClientError subclass based on metadata.retryable."""
    if metadata.retryable:
        raise RetryableHttpError(metadata)
    raise PermanentHttpError(metadata)


@dataclass(slots=True, frozen=True)
class HttpJsonResponse:
    payload: Any
    retries_used: int
    cache_hit: bool
    url: str


@dataclass(slots=True, frozen=True)
class _CacheEntry:
    expires_at: datetime
    payload: Any


class StructuredHttpClient:
    """Small HTTP JSON client with retry, jitter, cache, and structured errors."""

    def __init__(
        self,
        *,
        user_agent: str,
        contact: str = "",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        cache_ttl_sec: int = 600,
        openalex_api_key: str = "",
        enforce_allowed_hosts: bool = False,
        allowed_hosts: tuple[str, ...] = (),
        max_response_bytes: int = 1_048_576,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        self.user_agent = user_agent.strip() or "prediction-market-bot/0.1"
        self.contact = contact.strip()
        self.timeout_sec = min(max(timeout_sec, 0.1), 60.0)
        self.max_retries = min(max(max_retries, 0), 8)
        self.retry_backoff_sec = min(max(retry_backoff_sec, 0.0), 10.0)
        self.retry_jitter_sec = min(max(retry_jitter_sec, 0.0), 5.0)
        self.cache_ttl_sec = max(cache_ttl_sec, 0)
        self.openalex_api_key = openalex_api_key.strip()
        self.enforce_allowed_hosts = enforce_allowed_hosts
        self.allowed_hosts = tuple(host.strip().lower() for host in allowed_hosts if host.strip())
        self.max_response_bytes = max(max_response_bytes, 1)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or time.sleep
        self.random_fn = random_fn or random.random
        self._cache: dict[str, _CacheEntry] = {}
        self._circuit_breaker_threshold = 5  # consecutive failures before opening
        self._circuit_breaker_reset_sec = 60.0
        self._host_failures: dict[str, int] = {}
        self._host_open_until: dict[str, datetime] = {}

    def fetch_json(
        self,
        *,
        source: str,
        url: str,
        method: str = "GET",
        json_body: Mapping[str, Any] | list[Any] | None = None,
        query_params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
        cache_ttl_sec: int | None = None,
        timeout_sec: float | None = None,
        max_retries: int | None = None,
        retry_backoff_sec: float | None = None,
        retry_jitter_sec: float | None = None,
        add_openalex_auth: bool = False,
        api_key: str | None = None,
        api_key_header: str | None = None,
        api_key_prefix: str = "",
        require_api_key: bool = False,
    ) -> HttpJsonResponse:
        method_name = method.strip().upper() or "GET"
        effective_timeout = max(timeout_sec if timeout_sec is not None else self.timeout_sec, 0.1)
        retries = max(max_retries if max_retries is not None else self.max_retries, 0)
        backoff = max(retry_backoff_sec if retry_backoff_sec is not None else self.retry_backoff_sec, 0.0)
        jitter = max(retry_jitter_sec if retry_jitter_sec is not None else self.retry_jitter_sec, 0.0)
        ttl = max(cache_ttl_sec if cache_ttl_sec is not None else self.cache_ttl_sec, 0)

        final_url = self._build_url(url=url, query_params=query_params, add_openalex_auth=add_openalex_auth)
        cache_allowed = use_cache and method_name == "GET" and json_body is None
        cache_key = f"{method_name}|{final_url}"
        if cache_allowed and ttl > 0:
            cached = self._cache.get(cache_key)
            now = self.now_fn()
            if cached is not None and cached.expires_at >= now:
                return HttpJsonResponse(payload=cached.payload, retries_used=0, cache_hit=True, url=final_url)

        resolved_api_key = (api_key or "").strip()
        resolved_api_key_header = (api_key_header or "").strip()
        if require_api_key and not resolved_api_key:
            metadata = HttpErrorMetadata(
                source=source,
                method=method_name,
                url=final_url,
                attempt=0,
                max_retries=retries,
                timeout_sec=effective_timeout,
                status_code=None,
                retryable=False,
                classification="auth_config_error",
                reason_code="missing_api_key",
                error_type="ConfigError",
                message="required_api_key_missing",
                timestamp=self.now_fn().isoformat(),
            )
            _raise_http_error(metadata)

        self._enforce_outbound_policy(
            source=source,
            method=method_name,
            url=final_url,
            max_retries=retries,
            timeout_sec=effective_timeout,
        )
        self._check_circuit_breaker(
            source=source,
            method=method_name,
            url=final_url,
            max_retries=retries,
            timeout_sec=effective_timeout,
        )
        request_headers = self._build_headers(
            headers=headers,
            api_key=resolved_api_key,
            api_key_header=resolved_api_key_header,
            api_key_prefix=api_key_prefix,
        )
        body_bytes: bytes | None = None
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        retries_used = 0
        for attempt in range(retries + 1):
            try:
                req = request.Request(
                    url=final_url,
                    headers=request_headers,
                    data=body_bytes,
                    method=method_name,
                )
                with request.urlopen(req, timeout=effective_timeout) as response:  # nosec B310
                    raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        metadata = HttpErrorMetadata(
                            source=source,
                            method=method_name,
                            url=final_url,
                            attempt=attempt,
                            max_retries=retries,
                            timeout_sec=effective_timeout,
                            status_code=None,
                            retryable=False,
                            classification="response_too_large",
                            reason_code="response_body_limit_exceeded",
                            error_type="ResponseTooLarge",
                            message=f"response exceeded {self.max_response_bytes} bytes",
                            timestamp=self.now_fn().isoformat(),
                        )
                        _raise_http_error(metadata)
                    payload = json.loads(raw.decode("utf-8"))
                if cache_allowed and ttl > 0:
                    self._cache[cache_key] = _CacheEntry(
                        expires_at=self.now_fn() + timedelta(seconds=ttl),
                        payload=payload,
                    )
                self._record_success(final_url)
                return HttpJsonResponse(
                    payload=payload,
                    retries_used=retries_used,
                    cache_hit=False,
                    url=final_url,
                )
            except (
                error.URLError,
                error.HTTPError,
                TimeoutError,
                socket.timeout,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as exc:
                metadata = self._build_error_metadata(
                    source=source,
                    method=method_name,
                    url=final_url,
                    attempt=attempt,
                    max_retries=retries,
                    timeout_sec=effective_timeout,
                    exc=exc,
                )
                if attempt >= retries or not metadata.retryable:
                    self._record_failure(final_url)
                    if metadata.retryable:
                        raise RetryableHttpError(metadata) from exc
                    raise PermanentHttpError(metadata) from exc
                retries_used += 1
                delay = backoff * (attempt + 1)
                if jitter > 0:
                    delay += self.random_fn() * jitter
                if delay > 0:
                    self.sleep_fn(delay)

        metadata = HttpErrorMetadata(
            source=source,
            method=method_name,
            url=final_url,
            attempt=retries,
            max_retries=retries,
            timeout_sec=effective_timeout,
            status_code=None,
            retryable=False,
            classification="unknown_error",
            reason_code="unexpected_retry_loop_exit",
            error_type="unknown",
            message="unexpected_retry_loop_exit",
            timestamp=self.now_fn().isoformat(),
        )
        self._record_failure(final_url)
        _raise_http_error(metadata)

    def _check_circuit_breaker(
        self,
        *,
        source: str,
        method: str,
        url: str,
        max_retries: int,
        timeout_sec: float,
    ) -> None:
        host = parse.urlparse(url).hostname or ""
        now = self.now_fn()
        open_until = self._host_open_until.get(host)
        if open_until is not None:
            if now < open_until:
                raise PermanentHttpError(
                    HttpErrorMetadata(
                        source=source,
                        method=method,
                        url=url,
                        attempt=0,
                        max_retries=max_retries,
                        timeout_sec=timeout_sec,
                        status_code=None,
                        retryable=False,
                        classification="circuit_breaker_open",
                        reason_code="host_circuit_breaker_open",
                        error_type="CircuitBreakerOpen",
                        message=f"circuit_breaker_open host={host} until={open_until.isoformat()}",
                        timestamp=now.isoformat(),
                    )
                )
            else:
                # Reset: allow a probe request
                del self._host_open_until[host]
                self._host_failures[host] = 0

    def _record_success(self, url: str) -> None:
        host = parse.urlparse(url).hostname or ""
        self._host_failures.pop(host, None)

    def _record_failure(self, url: str) -> None:
        host = parse.urlparse(url).hostname or ""
        count = self._host_failures.get(host, 0) + 1
        self._host_failures[host] = count
        if count >= self._circuit_breaker_threshold:
            self._host_open_until[host] = self.now_fn() + timedelta(
                seconds=self._circuit_breaker_reset_sec
            )

    def _build_url(
        self,
        *,
        url: str,
        query_params: Mapping[str, str] | None,
        add_openalex_auth: bool,
    ) -> str:
        parsed = parse.urlparse(url)
        existing_query = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))
        merged: dict[str, str] = {key: value for key, value in existing_query.items()}
        if query_params:
            for key, value in query_params.items():
                merged[str(key)] = str(value)
        if add_openalex_auth:
            if self.openalex_api_key and "api_key" not in merged:
                merged["api_key"] = self.openalex_api_key
            if self.contact and "mailto" not in merged:
                merged["mailto"] = self.contact
        query = parse.urlencode(merged, doseq=False)
        return parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                query,
                parsed.fragment,
            )
        )

    def _build_headers(
        self,
        *,
        headers: Mapping[str, str] | None,
        api_key: str,
        api_key_header: str,
        api_key_prefix: str,
    ) -> dict[str, str]:
        payload = {"Accept": "application/json", "User-Agent": self.user_agent_with_contact}
        if headers:
            for key, value in headers.items():
                payload[str(key)] = str(value)
        if api_key and api_key_header:
            payload[api_key_header] = f"{api_key_prefix}{api_key}"
        if self.contact:
            payload.setdefault("From", self.contact)
        return payload

    @property
    def user_agent_with_contact(self) -> str:
        if not self.contact:
            return self.user_agent
        return f"{self.user_agent} ({self.contact})"

    def _build_error_metadata(
        self,
        *,
        source: str,
        method: str,
        url: str,
        attempt: int,
        max_retries: int,
        timeout_sec: float,
        exc: Exception,
    ) -> HttpErrorMetadata:
        status_code: int | None = None
        retryable = True
        classification = "http_error"
        reason_code = "http_request_failed"
        if isinstance(exc, error.HTTPError):
            status_code = int(exc.code)
            retryable = status_code in {408, 425, 429, 500, 502, 503, 504}
            if status_code in {401, 407}:
                classification = "auth_config_error"
                reason_code = f"http_{status_code}_auth_error"
            elif status_code == 403:
                classification = "blocked_403"
                reason_code = "http_403_blocked"
            elif status_code == 408:
                classification = "timeout"
                reason_code = "http_timeout_408"
            elif status_code in {429, 500, 502, 503, 504}:
                classification = "upstream_error"
                reason_code = f"http_{status_code}_retryable"
            elif 400 <= status_code < 500:
                classification = "auth_config_error"
                reason_code = f"http_{status_code}_client_error"
            else:
                classification = "upstream_error"
                reason_code = f"http_{status_code}_server_error"
        elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
            retryable = False
            classification = "malformed_payload"
            reason_code = "malformed_json_payload"
        elif isinstance(exc, (TimeoutError, socket.timeout)):
            retryable = True
            classification = "timeout"
            reason_code = "socket_timeout"
        elif isinstance(exc, error.URLError):
            retryable = True
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                classification = "timeout"
                reason_code = "url_timeout"
            else:
                classification = "network_error"
                reason_code = "url_error"
        return HttpErrorMetadata(
            source=source,
            method=method,
            url=url,
            attempt=attempt,
            max_retries=max_retries,
            timeout_sec=timeout_sec,
            status_code=status_code,
            retryable=retryable,
            classification=classification,
            reason_code=reason_code,
            error_type=type(exc).__name__,
            message=str(exc),
            timestamp=self.now_fn().isoformat(),
        )

    def _enforce_outbound_policy(
        self,
        *,
        source: str,
        method: str,
        url: str,
        max_retries: int,
        timeout_sec: float,
    ) -> None:
        parsed = parse.urlparse(url)
        scheme = parsed.scheme.strip().lower()
        host = (parsed.hostname or "").strip().lower()
        if scheme not in {"http", "https"}:
            raise PermanentHttpError(
                HttpErrorMetadata(
                    source=source,
                    method=method,
                    url=url,
                    attempt=0,
                    max_retries=max_retries,
                    timeout_sec=timeout_sec,
                    status_code=None,
                    retryable=False,
                    classification="network_policy_violation",
                    reason_code="unsupported_url_scheme",
                    error_type="PolicyError",
                    message=f"unsupported_scheme={scheme or 'missing'}",
                    timestamp=self.now_fn().isoformat(),
                )
            )
        if self.enforce_allowed_hosts and not self._host_allowed(host):
            raise PermanentHttpError(
                HttpErrorMetadata(
                    source=source,
                    method=method,
                    url=url,
                    attempt=0,
                    max_retries=max_retries,
                    timeout_sec=timeout_sec,
                    status_code=None,
                    retryable=False,
                    classification="network_policy_violation",
                    reason_code="host_not_allowed",
                    error_type="PolicyError",
                    message=f"host_not_allowed={host or 'missing'}",
                    timestamp=self.now_fn().isoformat(),
                )
            )

    def _host_allowed(self, host: str) -> bool:
        if not host:
            return False
        if not self.allowed_hosts:
            return False
        for allowed in self.allowed_hosts:
            if host == allowed:
                return True
            if host.endswith(f".{allowed}"):
                return True
        return False
