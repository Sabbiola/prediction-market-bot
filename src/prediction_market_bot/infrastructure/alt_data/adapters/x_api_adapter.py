from __future__ import annotations

import json
import random
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable, Mapping
from urllib import error, parse, request

from prediction_market_bot.infrastructure.alt_data.x_models import (
    XFetchPage,
    XPostRecord,
    XQuery,
    XQueryKind,
    build_x_dedup_key,
    parse_datetime_utc,
)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class XSourceFetchError(RuntimeError):
    def __init__(self, *, source_id: str, reason_code: str, detail: str) -> None:
        super().__init__(f"x_source_fetch_error source={source_id} reason={reason_code} detail={detail}")
        self.source_id = source_id
        self.reason_code = reason_code
        self.detail = detail


@dataclass(slots=True, frozen=True)
class _FetchResult:
    payload: Any
    response_headers: Mapping[str, str]
    retries_used: int
    duration_ms: float


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in headers.items():
        payload[str(key).strip().lower()] = str(value).strip()
    return payload


def _default_fetch_json(url: str, timeout_sec: float, headers: Mapping[str, str]) -> tuple[Any, Mapping[str, str]]:
    req = request.Request(url=url, headers={str(k): str(v) for k, v in headers.items()}, method="GET")
    with request.urlopen(req, timeout=timeout_sec) as response:
        raw = response.read()
        response_headers = _normalize_headers(dict(response.headers.items()))
    payload = json.loads(raw.decode("utf-8"))
    return payload, response_headers


class XApiAdapter:
    def __init__(
        self,
        *,
        source_id: str,
        auth_token: str,
        auth_mode: str = "bearer",
        source_class: str = "x",
        source_name: str = "x_api_adapter",
        endpoint_url: str = "https://api.x.com",
        search_endpoint_path: str = "/2/tweets/search/recent",
        user_lookup_endpoint_path_template: str = "/2/users/by/username/{username}",
        user_posts_endpoint_path_template: str = "/2/users/{user_id}/tweets",
        user_agent: str = "prediction-market-bot/0.1",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
        fetch_json_fn: Callable[[str, float, Mapping[str, str]], tuple[Any, Mapping[str, str]]] | None = None,
    ) -> None:
        token = auth_token.strip()
        if not token:
            raise ValueError("auth_token is required for XApiAdapter.")
        mode = auth_mode.strip().lower()
        if mode not in {"bearer", "user_context"}:
            raise ValueError("auth_mode must be one of: bearer, user_context")
        self.source_id = source_id.strip().lower()
        self.source_class = source_class.strip().lower() or "x"
        self.source_name = source_name.strip() or "x_api_adapter"
        self.auth_mode = mode
        self.endpoint_url = (endpoint_url.strip() or "https://api.x.com").rstrip("/")
        self.search_endpoint_path = search_endpoint_path.strip()
        self.user_lookup_endpoint_path_template = user_lookup_endpoint_path_template.strip()
        self.user_posts_endpoint_path_template = user_posts_endpoint_path_template.strip()
        self.auth_token = token
        self.user_agent = user_agent.strip() or "prediction-market-bot/0.1"
        self.timeout_sec = max(timeout_sec, 0.1)
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_sec = max(retry_backoff_sec, 0.0)
        self.retry_jitter_sec = max(retry_jitter_sec, 0.0)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or time.sleep
        self.random_fn = random_fn or random.random
        self.fetch_json_fn = fetch_json_fn or _default_fetch_json

    @property
    def search_endpoint_available(self) -> bool:
        return bool(self.search_endpoint_path)

    @property
    def account_endpoints_available(self) -> bool:
        return bool(self.user_lookup_endpoint_path_template and self.user_posts_endpoint_path_template)

    def fetch_search(self, query: XQuery, *, limit: int = 50, cursor: str | None = None) -> XFetchPage:
        if query.kind != XQueryKind.KEYWORD:
            raise ValueError("fetch_search requires query.kind=keyword")
        if not self.search_endpoint_available:
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="search_endpoint_unavailable",
                detail="search_endpoint_path is empty",
            )
        started = perf_counter()
        params = {
            "query": query.value,
            "max_results": str(min(max(limit, 1), 100)),
            "tweet.fields": "author_id,created_at,conversation_id,lang,public_metrics",
            "expansions": "author_id",
            "user.fields": "username",
        }
        if cursor:
            params["next_token"] = cursor
        url = self._build_url(path=self.search_endpoint_path, params=params)
        result = self._fetch_json(url)
        payload = result.payload
        if not isinstance(payload, Mapping):
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_search_payload",
                detail="search payload is not a mapping",
            )
        users_by_id = self._parse_users(payload.get("includes"))
        records, next_cursor = self._parse_tweets(payload=payload, query=query, users_by_id=users_by_id)
        remaining, reset_epoch = self._parse_rate_limit_headers(result.response_headers)
        return XFetchPage(
            source_id=self.source_id,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=self.now_fn(),
            retries_used=result.retries_used,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            cache_hit=False,
            next_cursor=next_cursor,
            rate_limit_remaining=remaining,
            rate_limit_reset_epoch=reset_epoch,
            records=records,
        )

    def fetch_account_posts(self, query: XQuery, *, limit: int = 50, cursor: str | None = None) -> XFetchPage:
        if query.kind != XQueryKind.ACCOUNT:
            raise ValueError("fetch_account_posts requires query.kind=account")
        if not self.account_endpoints_available:
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="account_endpoint_unavailable",
                detail="account endpoint path template is empty",
            )
        started = perf_counter()
        username = query.value.strip().lstrip("@")
        if not username:
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="empty_query",
                detail="account query is empty",
            )
        user_lookup_path = self.user_lookup_endpoint_path_template.format(username=parse.quote(username, safe=""))
        user_lookup_url = self._build_url(path=user_lookup_path, params={"user.fields": "username"})
        user_lookup_result = self._fetch_json(user_lookup_url)
        user_lookup_payload = user_lookup_result.payload
        user_data = user_lookup_payload.get("data") if isinstance(user_lookup_payload, Mapping) else None
        if not isinstance(user_data, Mapping):
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="account_lookup_failed",
                detail=f"unable to resolve account={username}",
            )
        user_id = str(user_data.get("id") or "").strip()
        resolved_username = str(user_data.get("username") or username).strip()
        if not user_id:
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="account_lookup_failed",
                detail=f"missing user id for account={username}",
            )
        posts_path = self.user_posts_endpoint_path_template.format(user_id=parse.quote(user_id, safe=""))
        params = {
            "max_results": str(min(max(limit, 1), 100)),
            "tweet.fields": "author_id,created_at,conversation_id,lang,public_metrics",
        }
        if cursor:
            params["pagination_token"] = cursor
        posts_url = self._build_url(path=posts_path, params=params)
        posts_result = self._fetch_json(posts_url)
        payload = posts_result.payload
        if not isinstance(payload, Mapping):
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_account_payload",
                detail="account posts payload is not a mapping",
            )
        users_by_id = {user_id: resolved_username}
        records, next_cursor = self._parse_tweets(payload=payload, query=query, users_by_id=users_by_id)
        remaining, reset_epoch = self._parse_rate_limit_headers(posts_result.response_headers)
        retries_used = user_lookup_result.retries_used + posts_result.retries_used
        return XFetchPage(
            source_id=self.source_id,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=self.now_fn(),
            retries_used=retries_used,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            cache_hit=False,
            next_cursor=next_cursor,
            rate_limit_remaining=remaining,
            rate_limit_reset_epoch=reset_epoch,
            records=records,
        )

    def _parse_tweets(
        self,
        *,
        payload: Mapping[str, Any],
        query: XQuery,
        users_by_id: Mapping[str, str],
    ) -> tuple[tuple[XPostRecord, ...], str | None]:
        fetched_at_utc = self.now_fn()
        raw_rows = payload.get("data")
        rows = raw_rows if isinstance(raw_rows, list) else []
        parsed_rows: list[XPostRecord] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                continue
            post_id = str(raw_row.get("id") or "").strip()
            if not post_id:
                continue
            author_id = str(raw_row.get("author_id") or "").strip()
            author_username = users_by_id.get(author_id, "")
            created_at_utc = parse_datetime_utc(raw_row.get("created_at"))
            dedup_key = build_x_dedup_key(
                source_record_id=post_id,
                created_at_utc=created_at_utc,
                query=query,
            )
            post_url = self._build_post_url(author_username=author_username, post_id=post_id)
            public_metrics_raw = raw_row.get("public_metrics")
            public_metrics: dict[str, int] = {}
            if isinstance(public_metrics_raw, Mapping):
                for metric_key, metric_value in public_metrics_raw.items():
                    key_text = str(metric_key).strip()
                    if not key_text:
                        continue
                    try:
                        public_metrics[key_text] = int(metric_value)
                    except (TypeError, ValueError):
                        continue
            parsed_rows.append(
                XPostRecord(
                    source_id=self.source_id,
                    source_class=self.source_class,
                    source_name=self.source_name,
                    query=query.value,
                    query_kind=query.kind,
                    dedup_key=dedup_key,
                    source_record_id=post_id,
                    post_id=post_id,
                    post_url=post_url,
                    author_id=author_id,
                    author_username=author_username,
                    text=str(raw_row.get("text") or "").strip(),
                    lang=str(raw_row.get("lang") or "").strip(),
                    conversation_id=str(raw_row.get("conversation_id") or "").strip(),
                    public_metrics=public_metrics,
                    created_at_utc=created_at_utc,
                    fetched_at_utc=fetched_at_utc,
                    source_metadata={
                        "auth_mode": self.auth_mode,
                        "endpoint_url": self.endpoint_url,
                        "query_kind": query.kind.value,
                    },
                    raw_payload=dict(raw_row),
                )
            )
        meta = payload.get("meta")
        next_cursor = None
        if isinstance(meta, Mapping):
            next_token = meta.get("next_token") or meta.get("pagination_token")
            if isinstance(next_token, str) and next_token.strip():
                next_cursor = next_token.strip()
        return tuple(parsed_rows), next_cursor

    @staticmethod
    def _parse_users(raw_includes: Any) -> dict[str, str]:
        users_by_id: dict[str, str] = {}
        if not isinstance(raw_includes, Mapping):
            return users_by_id
        raw_users = raw_includes.get("users")
        if not isinstance(raw_users, list):
            return users_by_id
        for raw_user in raw_users:
            if not isinstance(raw_user, Mapping):
                continue
            user_id = str(raw_user.get("id") or "").strip()
            username = str(raw_user.get("username") or "").strip()
            if user_id and username:
                users_by_id[user_id] = username
        return users_by_id

    @staticmethod
    def _build_post_url(*, author_username: str, post_id: str) -> str:
        handle = author_username.strip() or "i"
        if handle == "i":
            return f"https://x.com/i/web/status/{post_id}"
        return f"https://x.com/{handle}/status/{post_id}"

    def _fetch_json(self, url: str) -> _FetchResult:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.auth_token}",
        }
        retries_used = 0
        started = perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                payload, response_headers = self.fetch_json_fn(url, self.timeout_sec, headers)
                return _FetchResult(
                    payload=payload,
                    response_headers=_normalize_headers(response_headers),
                    retries_used=retries_used,
                    duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
                )
            except error.HTTPError as exc:
                last_error = exc
                status_code = int(exc.code)
                retryable = status_code in _RETRYABLE_STATUS
                if attempt >= self.max_retries or not retryable:
                    raise XSourceFetchError(
                        source_id=self.source_id,
                        reason_code=f"http_{status_code}",
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                delay = self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)
            except (error.URLError, TimeoutError, socket.timeout, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    reason_code = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "fetch_failed"
                    raise XSourceFetchError(
                        source_id=self.source_id,
                        reason_code=reason_code,
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                delay = self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if attempt >= self.max_retries:
                    raise XSourceFetchError(
                        source_id=self.source_id,
                        reason_code="fetch_failed",
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                delay = self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)

        raise XSourceFetchError(
            source_id=self.source_id,
            reason_code="fetch_failed",
            detail=type(last_error).__name__ if last_error is not None else "unknown_error",
        )

    def _build_url(self, *, path: str, params: Mapping[str, str]) -> str:
        query = parse.urlencode({str(key): str(value) for key, value in params.items()}, doseq=False)
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.endpoint_url}{normalized_path}?{query}"

    @staticmethod
    def _parse_rate_limit_headers(headers: Mapping[str, str]) -> tuple[float | None, float | None]:
        remaining = XApiAdapter._parse_float(headers.get("x-rate-limit-remaining"))
        reset_epoch = XApiAdapter._parse_float(headers.get("x-rate-limit-reset"))
        return remaining, reset_epoch

    def _compute_retry_delay(self, attempt: int) -> float:
        delay = self.retry_backoff_sec * (attempt + 1)
        if self.retry_jitter_sec > 0:
            delay += self.random_fn() * self.retry_jitter_sec
        return max(delay, 0.0)

    @staticmethod
    def _parse_float(value: str | None) -> float | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
