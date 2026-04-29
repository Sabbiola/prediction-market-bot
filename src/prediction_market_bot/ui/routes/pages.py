"""Pages router — Fase 7 SPA cutover.

GET /        → serve React SPA (static/dist/index.html)
GET /login   → redirect to / (SPA handles auth)
POST /login  → legacy form-post auth (kept for backward-compat; SPA uses /api/auth/login)
POST /logout → form logout redirect (SPA uses /api/auth/logout)
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from prediction_market_bot.ui.dependencies import get_auth_service
from prediction_market_bot.ui.auth import UiAuthService

logger = logging.getLogger(__name__)

router = APIRouter()

_SPA_INDEX = Path(__file__).resolve().parent.parent / "static" / "dist" / "index.html"


def _serve_spa() -> Response:
    if _SPA_INDEX.exists():
        return FileResponse(str(_SPA_INDEX), media_type="text/html")
    return HTMLResponse(
        "<p>UI not built. Run <code>npm run build</code> inside <code>src/prediction_market_bot/ui/frontend/</code>.</p>",
        status_code=503,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:  # noqa: ARG001
    return _serve_spa()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:  # noqa: ARG001
    # SPA handles the login UI; redirect browser to root so SPA boots.
    return RedirectResponse(url="/", status_code=302)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/"),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    """Legacy form-based login — kept so curl/old bookmarks still work.

    The React SPA uses POST /api/auth/login (JSON) instead.
    """
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    safe_next = _safe_next_path(next_path)
    user = auth.authenticate_credentials(username=username, password=password)
    if user is None:
        return RedirectResponse(url="/login?error=1", status_code=303)
    auth.login(request, user)
    return RedirectResponse(url=safe_next, status_code=303)


@router.post("/logout")
def logout_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Legacy form-based logout. SPA uses POST /api/auth/logout."""
    auth.logout(request)
    return RedirectResponse(url="/", status_code=303)


def _safe_next_path(next_path: str | None) -> str:
    if next_path is None:
        return "/"
    value = next_path.strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value or "/"


@router.get("/trader", response_class=HTMLResponse)
def trader_redirect() -> Response:
    """Old Jinja2 trader page — replaced by React SPA. Redirect to root."""
    return RedirectResponse(url="/", status_code=301)


# ── Catch-all: serve SPA for any unmatched GET route ──────────────────────────
# REGISTERED LAST so specific routes (/trader, /login, /api/...) take precedence.
@router.get("/{path:path}", response_class=HTMLResponse)
def spa_catchall(path: str) -> Response:  # noqa: ARG001
    return _serve_spa()
