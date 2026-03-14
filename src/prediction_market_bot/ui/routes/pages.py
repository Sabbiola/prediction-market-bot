from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from prediction_market_bot.ui.dependencies import get_auth_service, get_read_model_service
from prediction_market_bot.ui.auth import UiAuthService
from prediction_market_bot.ui.read_models import UiReadModelService

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("UI templates not initialized")
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    user = auth.current_user(request)
    if user is not None:
        return RedirectResponse(url="/", status_code=303)
    next_path = request.query_params.get("next") or "/"
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "Operator Login",
            "auth_enabled": True,
            "auth_user": {},
            "error": "",
            "next_path": next_path,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/"),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    user = auth.authenticate_credentials(username=username, password=password)
    if user is None:
        templates = getattr(request.app.state, "templates", None)
        if not isinstance(templates, Jinja2Templates):
            raise RuntimeError("UI templates not initialized")
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "title": "Operator Login",
                "auth_enabled": True,
                "auth_user": {},
                "error": "invalid_credentials",
                "next_path": next_path or "/",
            },
            status_code=401,
        )
    auth.login(request, user)
    target = next_path.strip() or "/"
    return RedirectResponse(url=target, status_code=303)


@router.post("/logout")
def logout_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> RedirectResponse:
    auth.logout(request)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    service: UiReadModelService = Depends(get_read_model_service),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("UI templates not initialized")
    user = auth.current_user(request)
    if auth.enabled and user is None:
        return RedirectResponse(url="/login?next=/", status_code=303)

    run_id = request.query_params.get("run_id")
    review_status = request.query_params.get("review_status")
    review_queue_id = request.query_params.get("review_queue_id")
    tx_intent_id = request.query_params.get("tx_intent_id")
    active_tab = request.query_params.get("active_tab") or "overview"

    run_selector, run_selector_error = _safe_panel(lambda: service.run_selector(run_id=run_id).model_dump(mode="json"))
    overview, overview_error = _safe_panel(lambda: service.overview_tab(run_id=run_id).model_dump(mode="json"))
    system, system_error = _safe_panel(lambda: service.system_health_tab(run_id=run_id).model_dump(mode="json"))
    scanner, scanner_error = _safe_panel(lambda: service.scanner_tab(run_id=run_id).model_dump(mode="json"))
    research, research_error = _safe_panel(lambda: service.research_tab(run_id=run_id).model_dump(mode="json"))
    prediction, prediction_error = _safe_panel(lambda: service.prediction_tab(run_id=run_id).model_dump(mode="json"))
    risk, risk_error = _safe_panel(lambda: service.risk_tab(run_id=run_id).model_dump(mode="json"))
    execution, execution_error = _safe_panel(lambda: service.execution_tab(run_id=run_id).model_dump(mode="json"))
    settlement, settlement_error = _safe_panel(lambda: service.settlement_tab(run_id=run_id).model_dump(mode="json"))
    review_queue, review_queue_error = _safe_panel(
        lambda: service.review_queue_tab(
            run_id=run_id,
            status=review_status,
            queue_id=review_queue_id,
        ).model_dump(mode="json")
    )
    sandbox_tx, sandbox_tx_error = _safe_panel(
        lambda: service.sandbox_tx_tab(run_id=run_id, intent_id=tx_intent_id).model_dump(mode="json")
    )
    reports, reports_error = _safe_panel(lambda: service.reports_tab(run_id=run_id).model_dump(mode="json"))

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "Prediction Market Bot Control Plane",
            "auth_enabled": auth.enabled,
            "auth_user": {
                "username": user.username if user is not None else "",
                "role": user.role if user is not None else "",
                "session_expires_at": user.session_expires_at if user is not None else "",
            },
            "run_id": run_id or "",
            "active_tab": active_tab,
            "review_status": review_status or "PENDING_REVIEW",
            "review_queue_id": review_queue_id or "",
            "tx_intent_id": tx_intent_id or "",
            "run_selector": run_selector,
            "run_selector_error": run_selector_error,
            "overview": overview,
            "overview_error": overview_error,
            "system": system,
            "system_error": system_error,
            "scanner": scanner,
            "scanner_error": scanner_error,
            "research": research,
            "research_error": research_error,
            "prediction": prediction,
            "prediction_error": prediction_error,
            "risk": risk,
            "risk_error": risk_error,
            "execution": execution,
            "execution_error": execution_error,
            "settlement": settlement,
            "settlement_error": settlement_error,
            "review_queue": review_queue,
            "review_queue_error": review_queue_error,
            "sandbox_tx": sandbox_tx,
            "sandbox_tx_error": sandbox_tx_error,
            "reports": reports,
            "reports_error": reports_error,
        },
    )


def _safe_panel(loader: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], str]:
    try:
        return loader(), ""
    except Exception:
        return {}, "panel_temporarily_unavailable"
