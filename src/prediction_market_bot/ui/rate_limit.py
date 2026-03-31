"""Lightweight in-memory per-IP rate limiter for the API."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class _TokenBucket:
    tokens: float
    last_refill: float


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token bucket rate limiter.

    Skips rate limiting for health/ready/metrics endpoints.
    """

    def __init__(
        self,
        app,
        *,
        requests_per_second: float = 10.0,
        burst: int = 30,
        cleanup_interval: int = 300,
    ) -> None:
        super().__init__(app)
        self._rate = requests_per_second
        self._burst = burst
        self._cleanup_interval = cleanup_interval
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_exempt(self, path: str) -> bool:
        return path in ("/health", "/ready", "/metrics")

    def _cleanup_stale(self, now: float) -> None:
        """Remove buckets that have not been touched recently."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        stale_threshold = now - self._cleanup_interval
        stale_keys = [
            k for k, v in self._buckets.items() if v.last_refill < stale_threshold
        ]
        for k in stale_keys:
            del self._buckets[k]

    def _consume(self, ip: str) -> bool:
        """Try to consume one token for *ip*. Returns ``True`` on success."""
        now = time.monotonic()
        with self._lock:
            self._cleanup_stale(now)
            bucket = self._buckets.get(ip)
            if bucket is None:
                bucket = _TokenBucket(tokens=float(self._burst), last_refill=now)
                self._buckets[ip] = bucket

            # Refill tokens based on elapsed time.
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                float(self._burst), bucket.tokens + elapsed * self._rate
            )
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    # ------------------------------------------------------------------
    # Starlette dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._is_exempt(request.url.path):
            return await call_next(request)

        ip = self._get_client_ip(request)
        if not self._consume(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": "1"},
            )
        return await call_next(request)
