from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from prediction_market_bot.app.secrets import SecretResolutionError, build_secret_provider

from .actions import UiOperatorActionService
from .auth import UiAuthService
from .read_models import UiReadModelService, build_ui_runtime_context
from .routes import api_router, pages_router, trader_live_router

logger = logging.getLogger(__name__)


def create_web_app(
    *,
    config_path: str | Path = "",
    agents_config_path: str | Path = "",
) -> FastAPI:
    if not config_path:
        config_path = os.environ.get("PM_BOT_CONFIG_PATH", "config/app.yaml")
    if not agents_config_path:
        agents_config_path = os.environ.get("PM_BOT_AGENTS_CONFIG_PATH", "config/agents.yaml")
    context = build_ui_runtime_context(str(config_path), str(agents_config_path))
    app = FastAPI(
        title="Prediction Market Bot Control Plane",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.ui_context = context
    secrets = build_secret_provider(context.settings)
    app.state.secrets = secrets
    app.state.read_models = UiReadModelService(context)
    app.state.actions = UiOperatorActionService(context)
    auth = UiAuthService(context.settings, secrets)
    auth.validate_configuration()
    app.state.auth = auth
    # Jinja2 templates kept for backward-compat (e.g. tests that import them).
    # The SPA cutover in pages.py serves static/dist/index.html instead.
    _tmpl = Jinja2Templates(directory=str(_templates_dir()))
    _tmpl.env.filters["tojson"] = lambda v, **_: json.dumps(v, ensure_ascii=False)
    app.state.templates = _tmpl
    if context.settings.ui_auth.enabled:
        try:
            secret = secrets.get(
                context.settings.ui_auth.session_secret_env,
                required=True,
            )
        except SecretResolutionError as exc:
            raise ValueError(
                f"ui_auth enabled but session secret is missing: {context.settings.ui_auth.session_secret_env}"
            ) from exc
        app.add_middleware(
            SessionMiddleware,
            secret_key=secret,
            session_cookie=context.settings.ui_auth.cookie_name,
            max_age=context.settings.ui_auth.session_timeout_sec,
            same_site=_cookie_samesite(context.settings.ui_auth.cookie_samesite),
            https_only=context.settings.ui_auth.cookie_secure,
        )

    if context.settings.security.rate_limit.enabled:
        from prediction_market_bot.ui.rate_limit import RateLimitMiddleware

        app.add_middleware(
            RateLimitMiddleware,
            requests_per_second=context.settings.security.rate_limit.requests_per_second,
            burst=context.settings.security.rate_limit.burst,
        )

    @app.middleware("http")
    async def performance_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        response = await call_next(request)
        duration_ms = max((perf_counter() - started) * 1000.0, 0.0)
        path = request.url.path
        settings = context.settings

        if settings.performance.include_response_timing_headers and (
            path == "/health" or path == "/ready" or path.startswith("/api/")
        ):
            response.headers["X-Request-Duration-Ms"] = str(round(duration_ms, 3))
        if _is_polling_path(path):
            ttl_sec = max(settings.performance.ui_poll_cache_ttl_sec, 0.0)
            response.headers["Cache-Control"] = f"private, max-age={int(ttl_sec)}"
            base_poll_ms = int(round(settings.performance.ui_poll_min_interval_sec * 1000.0))
            response.headers["X-Poll-Suggested-Interval-Ms"] = str(
                _poll_suggested_interval_ms(path=path, base_interval_ms=base_poll_ms)
            )
        if settings.performance.slow_stage_threshold_ms > 0 and duration_ms >= settings.performance.slow_stage_threshold_ms:
            logger.warning(
                "ui_slow_request",
                extra={
                    "event": "ui_slow_request",
                    "path": path,
                    "method": request.method,
                    "duration_ms": round(duration_ms, 3),
                    "threshold_ms": round(settings.performance.slow_stage_threshold_ms, 3),
                    "status_code": response.status_code,
                },
            )
        return response

    if context.settings.security.secure_headers_enabled:

        @app.middleware("http")
        async def security_headers(
            request: Request, call_next: RequestResponseEndpoint
        ) -> Response:
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "img-src 'self' data:; "
                "font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self' https://api.coinbase.com https://gamma-api.polymarket.com; "
                "frame-ancestors 'none'"
            )
            return response

    static_dir = _static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(trader_live_router)
    app.include_router(pages_router)
    return app


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _cookie_samesite(value: str) -> Literal["lax", "strict", "none"]:
    normalized = value.strip().lower()
    if normalized == "strict":
        return "strict"
    if normalized == "none":
        return "none"
    return "lax"


def _is_polling_path(path: str) -> bool:
    return path == "/api/incidents" or path.startswith("/api/tabs/")


def _poll_suggested_interval_ms(*, path: str, base_interval_ms: int) -> int:
    if path in {
        "/api/tabs/scanner",
        "/api/tabs/research",
        "/api/tabs/prediction",
        "/api/tabs/risk",
        "/api/tabs/positions",
        "/api/tabs/settlement",
    }:
        return max(base_interval_ms * 2, base_interval_ms)
    if path == "/api/tabs/reports":
        return max(base_interval_ms * 3, base_interval_ms)
    return max(base_interval_ms, 100)
