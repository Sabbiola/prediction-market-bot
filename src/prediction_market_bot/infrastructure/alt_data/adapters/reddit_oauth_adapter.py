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

from prediction_market_bot.infrastructure.alt_data.reddit_models import (
    RedditEvidenceKind,
    RedditEvidenceRecord,
    RedditFetchPage,
    RedditQuery,
    RedditQueryKind,
    build_reddit_dedup_key,
    parse_epoch_utc,
)

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RedditSourceFetchError(RuntimeError):
    def __init__(self, *, source_id: str, reason_code: str, detail: str) -> None:
        super().__init__(f"reddit_source_fetch_error source={source_id} reason={reason_code} detail={detail}")
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
    with request.urlopen(req, timeout=timeout_sec) as response:  # nosec B310
        raw = response.read()
        response_headers = _normalize_headers(dict(response.headers.items()))
    payload = json.loads(raw.decode("utf-8"))
    return payload, response_headers


class RedditOAuthAdapter:
    def __init__(
        self,
        *,
        source_id: str,
        oauth_token: str,
        source_class: str = "reddit",
        source_name: str = "reddit_oauth_adapter",
        endpoint_url: str = "https://oauth.reddit.com",
        user_agent: str = "prediction-market-bot/0.1",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        max_rate_limit_wait_sec: float = 30.0,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
        fetch_json_fn: Callable[[str, float, Mapping[str, str]], tuple[Any, Mapping[str, str]]] | None = None,
    ) -> None:
        token = oauth_token.strip()
        if not token:
            raise ValueError("oauth_token is required for RedditOAuthAdapter.")
        self.source_id = source_id.strip().lower()
        self.source_class = source_class.strip().lower() or "reddit"
        self.source_name = source_name.strip() or "reddit_oauth_adapter"
        self.endpoint_url = (endpoint_url.strip() or "https://oauth.reddit.com").rstrip("/")
        self.oauth_token = token
        self.user_agent = user_agent.strip() or "prediction-market-bot/0.1"
        self.timeout_sec = max(timeout_sec, 0.1)
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_sec = max(retry_backoff_sec, 0.0)
        self.retry_jitter_sec = max(retry_jitter_sec, 0.0)
        self.max_rate_limit_wait_sec = max(max_rate_limit_wait_sec, 0.0)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or time.sleep
        self.random_fn = random_fn or random.random
        self.fetch_json_fn = fetch_json_fn or _default_fetch_json
        self._subreddit_metadata_cache: dict[str, dict[str, Any]] = {}

    def verify_oauth(self) -> dict[str, Any]:
        url = self._build_url(path="/api/v1/me", params={"raw_json": "1"})
        result = self._fetch_json(url)
        headers = result.response_headers
        remaining, reset_sec, used = self._parse_rate_limit_headers(headers)
        self._respect_rate_limit_headers(remaining=remaining, reset_sec=reset_sec)
        payload = result.payload
        if not isinstance(payload, Mapping):
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_verify_payload",
                detail="oauth verify payload is not a mapping",
            )
        username = str(payload.get("name") or "").strip()
        if not username:
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="missing_username",
                detail="oauth verify payload missing user name",
            )
        return {
            "username": username,
            "retries_used": result.retries_used,
            "duration_ms": result.duration_ms,
            "rate_limit_remaining": remaining,
            "rate_limit_reset_sec": reset_sec,
            "rate_limit_used": used,
        }

    def fetch_submissions(
        self,
        query: RedditQuery,
        *,
        limit: int = 50,
        after: str | None = None,
    ) -> RedditFetchPage:
        started = perf_counter()
        if query.kind == RedditQueryKind.SUBREDDIT:
            subreddit = query.value.strip().lstrip("r/").strip()
            if not subreddit:
                raise RedditSourceFetchError(
                    source_id=self.source_id,
                    reason_code="empty_query",
                    detail="subreddit query is empty",
                )
            path = f"/r/{parse.quote(subreddit, safe='')}/new"
            params = {"raw_json": "1", "limit": str(max(limit, 1))}
            query_value = subreddit
        else:
            keyword = query.value.strip()
            if not keyword:
                raise RedditSourceFetchError(
                    source_id=self.source_id,
                    reason_code="empty_query",
                    detail="keyword query is empty",
                )
            path = "/search"
            params = {
                "raw_json": "1",
                "limit": str(max(limit, 1)),
                "sort": "new",
                "type": "link",
                "q": keyword,
            }
            query_value = keyword
        if after:
            params["after"] = after
        url = self._build_url(path=path, params=params)
        result = self._fetch_json(url)
        payload = result.payload
        if not isinstance(payload, Mapping):
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_listing_payload",
                detail="reddit listing payload is not a mapping",
            )
        listing = payload.get("data")
        if not isinstance(listing, Mapping):
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_listing_data",
                detail="reddit listing payload missing data",
            )
        children = listing.get("children")
        rows = children if isinstance(children, list) else []
        fetched_at_utc = self.now_fn()
        records: list[RedditEvidenceRecord] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                continue
            if str(raw_row.get("kind") or "").strip().lower() != "t3":
                continue
            raw_data = raw_row.get("data")
            if not isinstance(raw_data, Mapping):
                continue
            record = self._normalize_submission(
                query=RedditQuery(value=query_value, kind=query.kind),
                payload=raw_data,
                fetched_at_utc=fetched_at_utc,
            )
            if record is not None:
                records.append(record)

        next_cursor_raw = listing.get("after")
        next_cursor = str(next_cursor_raw).strip() if isinstance(next_cursor_raw, str) and next_cursor_raw.strip() else None
        remaining, reset_sec, used = self._parse_rate_limit_headers(result.response_headers)
        self._respect_rate_limit_headers(remaining=remaining, reset_sec=reset_sec)
        return RedditFetchPage(
            source_id=self.source_id,
            source_name=self.source_name,
            query=query_value,
            query_kind=query.kind,
            fetched_at_utc=fetched_at_utc,
            retries_used=result.retries_used,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            cache_hit=False,
            next_cursor=next_cursor,
            rate_limit_remaining=remaining,
            rate_limit_reset_sec=reset_sec,
            rate_limit_used=used,
            records=tuple(records),
        )

    def fetch_comments(
        self,
        *,
        post_id: str,
        query: RedditQuery,
        limit: int = 25,
    ) -> RedditFetchPage:
        started = perf_counter()
        canonical_post_id = post_id.strip().replace("t3_", "")
        if not canonical_post_id:
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="missing_post_id",
                detail="comments fetch requires a post id",
            )
        url = self._build_url(
            path=f"/comments/{parse.quote(canonical_post_id, safe='')}",
            params={
                "raw_json": "1",
                "sort": "new",
                "limit": str(max(limit, 1)),
                "depth": "1",
            },
        )
        result = self._fetch_json(url)
        payload = result.payload
        if not isinstance(payload, list) or len(payload) < 2:
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_comment_listing",
                detail="comments payload is not a two-listing reddit response",
            )
        comments_listing = payload[1]
        comments_data = comments_listing.get("data") if isinstance(comments_listing, Mapping) else None
        children = comments_data.get("children") if isinstance(comments_data, Mapping) else None
        rows = children if isinstance(children, list) else []
        fetched_at_utc = self.now_fn()
        records: list[RedditEvidenceRecord] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                continue
            if str(raw_row.get("kind") or "").strip().lower() != "t1":
                continue
            raw_data = raw_row.get("data")
            if not isinstance(raw_data, Mapping):
                continue
            record = self._normalize_comment(
                query=query,
                payload=raw_data,
                fetched_at_utc=fetched_at_utc,
                fallback_post_id=canonical_post_id,
            )
            if record is not None:
                records.append(record)
        remaining, reset_sec, used = self._parse_rate_limit_headers(result.response_headers)
        self._respect_rate_limit_headers(remaining=remaining, reset_sec=reset_sec)
        return RedditFetchPage(
            source_id=self.source_id,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=fetched_at_utc,
            retries_used=result.retries_used,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=remaining,
            rate_limit_reset_sec=reset_sec,
            rate_limit_used=used,
            records=tuple(records),
        )

    def fetch_subreddit_metadata(self, subreddit: str) -> dict[str, Any]:
        key = subreddit.strip().lower().lstrip("r/")
        if not key:
            return {}
        cached = self._subreddit_metadata_cache.get(key)
        if cached is not None:
            return dict(cached)
        url = self._build_url(path=f"/r/{parse.quote(key, safe='')}/about", params={"raw_json": "1"})
        result = self._fetch_json(url)
        payload = result.payload
        if not isinstance(payload, Mapping):
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_subreddit_payload",
                detail=f"subreddit about payload is not a mapping for r/{key}",
            )
        raw_data = payload.get("data")
        if not isinstance(raw_data, Mapping):
            raise RedditSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_subreddit_data",
                detail=f"subreddit about data missing for r/{key}",
            )
        metadata = {
            "subreddit": str(raw_data.get("display_name") or key),
            "subreddit_id": str(raw_data.get("name") or raw_data.get("id") or ""),
            "title": str(raw_data.get("title") or ""),
            "public_description": str(raw_data.get("public_description") or ""),
            "subscribers": int(raw_data.get("subscribers") or 0),
            "url": str(raw_data.get("url") or ""),
            "over18": bool(raw_data.get("over18") or False),
            "fetched_at_utc": self.now_fn().isoformat(),
        }
        self._subreddit_metadata_cache[key] = dict(metadata)
        remaining, reset_sec, _ = self._parse_rate_limit_headers(result.response_headers)
        self._respect_rate_limit_headers(remaining=remaining, reset_sec=reset_sec)
        return dict(metadata)

    def _normalize_submission(
        self,
        *,
        query: RedditQuery,
        payload: Mapping[str, Any],
        fetched_at_utc: datetime,
    ) -> RedditEvidenceRecord | None:
        post_id = str(payload.get("id") or "").strip()
        source_record_id = str(payload.get("name") or f"t3_{post_id}" if post_id else "").strip()
        if not source_record_id:
            return None
        subreddit = str(payload.get("subreddit") or "").strip()
        subreddit_id = str(payload.get("subreddit_id") or "").strip()
        permalink_url = self._normalize_permalink(payload.get("permalink"), post_id=post_id)
        created_at_utc = parse_epoch_utc(payload.get("created_utc"))
        dedup_key = build_reddit_dedup_key(
            evidence_kind=RedditEvidenceKind.SUBMISSION,
            source_record_id=source_record_id,
            permalink_url=permalink_url,
            created_at_utc=created_at_utc,
            query=query,
        )
        subreddit_metadata = self.fetch_subreddit_metadata(subreddit) if subreddit else {}
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("selftext") or "").strip()
        return RedditEvidenceRecord(
            source_id=self.source_id,
            source_class=self.source_class,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            evidence_kind=RedditEvidenceKind.SUBMISSION,
            dedup_key=dedup_key,
            source_record_id=source_record_id,
            post_id=post_id,
            comment_id="",
            parent_post_id=post_id,
            subreddit=subreddit,
            subreddit_id=subreddit_id,
            title=title,
            body=body,
            author=str(payload.get("author") or "").strip(),
            score=int(payload.get("score") or 0),
            num_comments=int(payload.get("num_comments") or 0),
            permalink_url=permalink_url,
            external_url=str(payload.get("url") or "").strip(),
            created_at_utc=created_at_utc,
            fetched_at_utc=fetched_at_utc,
            subreddit_metadata=subreddit_metadata,
            source_metadata={
                "query_kind": query.kind.value,
                "is_self": bool(payload.get("is_self") or False),
                "domain": str(payload.get("domain") or "").strip(),
            },
            raw_payload=dict(payload),
        )

    def _normalize_comment(
        self,
        *,
        query: RedditQuery,
        payload: Mapping[str, Any],
        fetched_at_utc: datetime,
        fallback_post_id: str,
    ) -> RedditEvidenceRecord | None:
        comment_id = str(payload.get("id") or "").strip()
        source_record_id = str(payload.get("name") or f"t1_{comment_id}" if comment_id else "").strip()
        if not source_record_id:
            return None
        post_id = str(payload.get("link_id") or "").strip().replace("t3_", "") or fallback_post_id
        subreddit = str(payload.get("subreddit") or "").strip()
        subreddit_id = str(payload.get("subreddit_id") or "").strip()
        permalink_url = self._normalize_permalink(payload.get("permalink"), post_id=post_id)
        created_at_utc = parse_epoch_utc(payload.get("created_utc"))
        dedup_key = build_reddit_dedup_key(
            evidence_kind=RedditEvidenceKind.COMMENT,
            source_record_id=source_record_id,
            permalink_url=permalink_url,
            created_at_utc=created_at_utc,
            query=query,
        )
        subreddit_metadata = self.fetch_subreddit_metadata(subreddit) if subreddit else {}
        return RedditEvidenceRecord(
            source_id=self.source_id,
            source_class=self.source_class,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            evidence_kind=RedditEvidenceKind.COMMENT,
            dedup_key=dedup_key,
            source_record_id=source_record_id,
            post_id=post_id,
            comment_id=comment_id,
            parent_post_id=str(payload.get("parent_id") or "").strip().replace("t3_", ""),
            subreddit=subreddit,
            subreddit_id=subreddit_id,
            title="",
            body=str(payload.get("body") or "").strip(),
            author=str(payload.get("author") or "").strip(),
            score=int(payload.get("score") or 0),
            num_comments=None,
            permalink_url=permalink_url,
            external_url=permalink_url,
            created_at_utc=created_at_utc,
            fetched_at_utc=fetched_at_utc,
            subreddit_metadata=subreddit_metadata,
            source_metadata={
                "query_kind": query.kind.value,
                "link_id": str(payload.get("link_id") or "").strip(),
                "depth": int(payload.get("depth") or 0),
            },
            raw_payload=dict(payload),
        )

    @staticmethod
    def _normalize_permalink(value: Any, *, post_id: str) -> str:
        text = str(value or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            return text
        if text.startswith("/"):
            return f"https://www.reddit.com{text}"
        if post_id:
            return f"https://www.reddit.com/comments/{post_id}"
        return "https://www.reddit.com/"

    def _fetch_json(self, url: str) -> _FetchResult:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.oauth_token}",
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
                    raise RedditSourceFetchError(
                        source_id=self.source_id,
                        reason_code=f"http_{status_code}",
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                response_headers = _normalize_headers(dict(exc.headers.items())) if exc.headers else {}
                retry_after = self._parse_retry_after_seconds(response_headers.get("retry-after"))
                delay = retry_after if retry_after is not None else self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)
            except (error.URLError, TimeoutError, socket.timeout, UnicodeDecodeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    reason_code = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "fetch_failed"
                    raise RedditSourceFetchError(
                        source_id=self.source_id,
                        reason_code=reason_code,
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                delay = self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)
            except Exception as exc:  # pragma: no cover - safety net for unexpected provider errors
                last_error = exc
                if attempt >= self.max_retries:
                    raise RedditSourceFetchError(
                        source_id=self.source_id,
                        reason_code="fetch_failed",
                        detail=type(exc).__name__,
                    ) from exc
                retries_used += 1
                delay = self._compute_retry_delay(attempt)
                if delay > 0:
                    self.sleep_fn(delay)

        raise RedditSourceFetchError(
            source_id=self.source_id,
            reason_code="fetch_failed",
            detail=type(last_error).__name__ if last_error is not None else "unknown_error",
        )

    def _build_url(self, *, path: str, params: Mapping[str, str]) -> str:
        query = parse.urlencode({str(key): str(value) for key, value in params.items()}, doseq=False)
        return f"{self.endpoint_url}{path}?{query}"

    def _parse_rate_limit_headers(self, headers: Mapping[str, str]) -> tuple[float | None, float | None, float | None]:
        remaining = self._parse_float(headers.get("x-ratelimit-remaining"))
        reset_sec = self._parse_float(headers.get("x-ratelimit-reset"))
        used = self._parse_float(headers.get("x-ratelimit-used"))
        return remaining, reset_sec, used

    def _respect_rate_limit_headers(self, *, remaining: float | None, reset_sec: float | None) -> None:
        if remaining is None or remaining > 0:
            return
        wait_sec = min(max(reset_sec or 0.0, 0.0), self.max_rate_limit_wait_sec)
        if wait_sec > 0:
            self.sleep_fn(wait_sec)

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

    @staticmethod
    def _parse_retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            return max(float(text), 0.0)
        except ValueError:
            return None
