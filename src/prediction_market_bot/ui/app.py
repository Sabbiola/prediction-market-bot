from __future__ import annotations

from pathlib import Path
import os
from typing import Literal

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .actions import UiOperatorActionService
from .auth import UiAuthService
from .read_models import UiReadModelService, build_ui_runtime_context
from .routes import api_router, pages_router


def create_web_app(
    *,
    config_path: str | Path = "config/app.yaml",
    agents_config_path: str | Path = "config/agents.yaml",
) -> FastAPI:
    context = build_ui_runtime_context(str(config_path), str(agents_config_path))
    app = FastAPI(
        title="Prediction Market Bot Control Plane",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    app.state.ui_context = context
    app.state.read_models = UiReadModelService(context)
    app.state.actions = UiOperatorActionService(context)
    app.state.auth = UiAuthService(context.settings)
    app.state.templates = Jinja2Templates(directory=str(_templates_dir()))
    if context.settings.ui_auth.enabled:
        secret = os.getenv(context.settings.ui_auth.session_secret_env, "").strip()
        if not secret:
            raise ValueError(
                f"ui_auth enabled but session secret env is missing: {context.settings.ui_auth.session_secret_env}"
            )
        app.add_middleware(
            SessionMiddleware,
            secret_key=secret,
            session_cookie=context.settings.ui_auth.cookie_name,
            max_age=context.settings.ui_auth.session_timeout_sec,
            same_site=_cookie_samesite(context.settings.ui_auth.cookie_samesite),
            https_only=context.settings.ui_auth.cookie_secure,
        )
    app.include_router(api_router)
    app.include_router(pages_router)
    return app


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _cookie_samesite(value: str) -> Literal["lax", "strict", "none"]:
    normalized = value.strip().lower()
    if normalized == "strict":
        return "strict"
    if normalized == "none":
        return "none"
    return "lax"
