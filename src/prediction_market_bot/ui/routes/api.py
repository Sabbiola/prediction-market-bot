from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

from prediction_market_bot.ui.actions import UiOperatorActionService
from prediction_market_bot.ui.auth import AuthenticatedUser, UiAuthService
from prediction_market_bot.ui.dependencies import (
    get_action_service,
    get_auth_service,
    get_read_model_service,
    require_admin_role,
    require_operator_role,
    require_viewer_role,
)
from prediction_market_bot.ui.models import (
    AdminSettingsActionRequest,
    AuthSessionResponse,
    ExecutionTabResponse,
    HealthResponse,
    IncidentsFeedResponse,
    LoginRequest,
    LoginResponse,
    OperatorActionResponse,
    OverviewTabResponse,
    PauseActionRequest,
    PredictionTabResponse,
    PositionsTabResponse,
    ReadinessResponse,
    ReviewDecisionActionRequest,
    ReviewQueueTabResponse,
    ReportsTabResponse,
    ResearchTabResponse,
    RiskTabResponse,
    RunOnceActionRequest,
    SandboxTxTabResponse,
    ScannerTabResponse,
    SettlementTabResponse,
    SystemHealthTabResponse,
    TxReconcileActionRequest,
    TxResubmitSafeActionRequest,
)
from prediction_market_bot.ui.read_models import UiReadModelService

router = APIRouter()


@router.post("/api/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> LoginResponse:
    user = auth.authenticate_credentials(username=payload.username, password=payload.password)
    if user is None:
        return LoginResponse(
            authenticated=False,
            status="failed",
            message="invalid_credentials",
            user=AuthSessionResponse(authenticated=False),
        )
    auth.login(request, user)
    return LoginResponse(
        authenticated=True,
        status="completed",
        message="login_successful",
        user=AuthSessionResponse(
            authenticated=True,
            username=user.username,
            role=user.role,
            session_expires_at=user.session_expires_at,
        ),
    )


@router.post("/api/auth/logout", response_model=OperatorActionResponse)
def logout(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> OperatorActionResponse:
    user = auth.current_user(request)
    auth.logout(request)
    return OperatorActionResponse(
        action="logout",
        accepted=True,
        status="completed",
        message="logout_successful",
        acting_user=user.username if user is not None else "",
        acting_role=user.role if user is not None else "",
    )


@router.get("/api/auth/session", response_model=AuthSessionResponse)
def session_info(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> AuthSessionResponse:
    user = auth.current_user(request)
    if user is None:
        return AuthSessionResponse(authenticated=False)
    return AuthSessionResponse(
        authenticated=True,
        username=user.username,
        role=user.role,
        session_expires_at=user.session_expires_at,
    )


@router.get("/health", response_model=HealthResponse)
def health(service: UiReadModelService = Depends(get_read_model_service)) -> HealthResponse:
    return service.health()


@router.get("/ready", response_model=ReadinessResponse)
def ready(
    response: Response,
    service: UiReadModelService = Depends(get_read_model_service),
) -> ReadinessResponse:
    payload = service.readiness()
    if not payload.ready:
        response.status_code = 503
    return payload


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(
    service: UiReadModelService = Depends(get_read_model_service),
) -> PlainTextResponse:
    text = service.prometheus_metrics_text()
    return PlainTextResponse(
        content=text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/api/tabs/overview", response_model=OverviewTabResponse)
def overview_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> OverviewTabResponse:
    return service.overview_tab(run_id=run_id)


@router.get("/api/tabs/system", response_model=SystemHealthTabResponse)
def system_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> SystemHealthTabResponse:
    return service.system_health_tab(run_id=run_id)


@router.get("/api/tabs/scanner", response_model=ScannerTabResponse)
def scanner_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> ScannerTabResponse:
    return service.scanner_tab(run_id=run_id)


@router.get("/api/tabs/research", response_model=ResearchTabResponse)
def research_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> ResearchTabResponse:
    return service.research_tab(run_id=run_id)


@router.get("/api/tabs/prediction", response_model=PredictionTabResponse)
def prediction_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> PredictionTabResponse:
    return service.prediction_tab(run_id=run_id)


@router.get("/api/tabs/risk", response_model=RiskTabResponse)
def risk_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> RiskTabResponse:
    return service.risk_tab(run_id=run_id)


@router.get("/api/tabs/execution", response_model=ExecutionTabResponse)
def execution_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> ExecutionTabResponse:
    return service.execution_tab(run_id=run_id)


@router.get("/api/tabs/positions", response_model=PositionsTabResponse)
def positions_tab(
    run_id: str | None = Query(default=None),
    market_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> PositionsTabResponse:
    return service.positions_tab(run_id=run_id, market_id=market_id)


@router.get("/api/tabs/settlement", response_model=SettlementTabResponse)
def settlement_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> SettlementTabResponse:
    return service.settlement_tab(run_id=run_id)


@router.get("/api/tabs/sandbox-tx", response_model=SandboxTxTabResponse)
def sandbox_tx_tab(
    run_id: str | None = Query(default=None),
    intent_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> SandboxTxTabResponse:
    return service.sandbox_tx_tab(run_id=run_id, intent_id=intent_id)


@router.get("/api/tabs/reports", response_model=ReportsTabResponse)
def reports_tab(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> ReportsTabResponse:
    return service.reports_tab(run_id=run_id)


@router.get("/api/incidents", response_model=IncidentsFeedResponse)
def incidents_feed(
    run_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> IncidentsFeedResponse:
    return service.incidents_feed(run_id=run_id, limit=limit)


@router.get("/api/incidents/stream")
async def incidents_stream(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> StreamingResponse:
    """SSE stream of incident events. Polls internal state and pushes new events."""

    async def event_generator():
        last_count = 0
        while True:
            try:
                feed = service.incidents_feed(run_id=run_id, limit=50)
                current_count = len(feed.rows)
                if current_count != last_count:
                    # Send the full current feed as an SSE event
                    data = json.dumps({
                        "generated_at": feed.generated_at,
                        "run_id": feed.run_id,
                        "count": current_count,
                        "rows": [
                            {
                                "timestamp": row.timestamp,
                                "event_type": row.event_type,
                                "severity": row.severity,
                                "summary": row.summary,
                                "component": row.component,
                                "affected_target": row.affected_target,
                                "reason_code": row.reason_code,
                                "run_url": row.run_url,
                                "review_queue_url": row.review_queue_url,
                                "sandbox_tx_url": row.sandbox_tx_url,
                            }
                            for row in feed.rows
                        ],
                    }, default=str)
                    yield f"event: incidents\ndata: {data}\n\n"
                    last_count = current_count
                else:
                    # Send keepalive comment
                    yield ": keepalive\n\n"
            except Exception:
                yield f"event: error\ndata: {{\"message\": \"internal_error\"}}\n\n"
                break
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/tabs/review-queue", response_model=ReviewQueueTabResponse)
def review_queue_tab(
    run_id: str | None = Query(default=None),
    status: str | None = Query(default="PENDING_REVIEW"),
    queue_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> ReviewQueueTabResponse:
    return service.review_queue_tab(
        run_id=run_id,
        status=status,
        queue_id=queue_id,
        limit=limit,
    )


@router.get("/api/overview", response_model=OverviewTabResponse)
def overview_legacy_alias(
    run_id: str | None = Query(default=None),
    service: UiReadModelService = Depends(get_read_model_service),
    _: AuthenticatedUser = Depends(require_viewer_role),
) -> OverviewTabResponse:
    return service.overview_tab(run_id=run_id)


@router.post("/api/actions/run-once", response_model=OperatorActionResponse)
def run_once(
    payload: RunOnceActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_operator_role),
) -> OperatorActionResponse:
    result = actions.run_once(
        run_id=payload.run_id,
        force=payload.force,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/pause", response_model=OperatorActionResponse)
def pause(
    payload: PauseActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_admin_role),
) -> OperatorActionResponse:
    result = actions.pause(reason=payload.reason, acting_user=user.username, acting_role=user.role)
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/resume", response_model=OperatorActionResponse)
def resume(
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_admin_role),
) -> OperatorActionResponse:
    result = actions.resume(acting_user=user.username, acting_role=user.role)
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/review-approve", response_model=OperatorActionResponse)
def review_approve(
    payload: ReviewDecisionActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_operator_role),
) -> OperatorActionResponse:
    result = actions.review_approve(
        queue_id=payload.queue_id,
        operator_id=payload.operator_id,
        rationale=payload.rationale,
        note=payload.note,
        confirmed=payload.confirm,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/review-reject", response_model=OperatorActionResponse)
def review_reject(
    payload: ReviewDecisionActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_operator_role),
) -> OperatorActionResponse:
    result = actions.review_reject(
        queue_id=payload.queue_id,
        operator_id=payload.operator_id,
        rationale=payload.rationale,
        note=payload.note,
        confirmed=payload.confirm,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/tx-reconcile", response_model=OperatorActionResponse)
def tx_reconcile(
    payload: TxReconcileActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_operator_role),
) -> OperatorActionResponse:
    result = actions.tx_reconcile(
        run_id=payload.run_id,
        intent_id=payload.intent_id,
        limit=payload.limit,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/tx-resubmit-safe", response_model=OperatorActionResponse)
def tx_resubmit_safe(
    payload: TxResubmitSafeActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_admin_role),
) -> OperatorActionResponse:
    result = actions.tx_resubmit_safe(
        intent_id=payload.intent_id,
        confirmed=payload.confirm,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result


@router.post("/api/actions/admin-settings", response_model=OperatorActionResponse)
def admin_settings(
    payload: AdminSettingsActionRequest,
    actions: UiOperatorActionService = Depends(get_action_service),
    read_models: UiReadModelService = Depends(get_read_model_service),
    user: AuthenticatedUser = Depends(require_admin_role),
) -> OperatorActionResponse:
    result = actions.admin_settings(
        setting=payload.setting,
        value=payload.value,
        confirmed=payload.confirm,
        acting_user=user.username,
        acting_role=user.role,
    )
    read_models.invalidate_poll_cache()
    return result
