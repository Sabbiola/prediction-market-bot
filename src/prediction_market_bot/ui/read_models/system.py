from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.services import (
    load_operator_state,
    operator_state_path,
    validate_startup,
)
from prediction_market_bot.ui.models import (
    DbConnectivitySummaryResponse,
    HealthResponse,
    HealthcheckSummaryResponse,
    ReadinessCheckResponse,
    ReadinessResponse,
    RunSelectorResponse,
    RuntimeMetricsResponse,
    StartupValidationSummaryResponse,
    SystemHealthTabResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import incident_banners, provider_status


def build_health(*, context: UiRuntimeContext) -> HealthResponse:
    settings = context.settings
    return HealthResponse(
        status="ok",
        app_name=settings.runtime.app_name,
        environment=settings.runtime.env,
        runtime_mode=settings.runtime.mode.value,
        execution_mode=settings.execution.mode.value,
        timestamp_utc=datetime.now(UTC).isoformat(),
    )


def build_readiness(*, context: UiRuntimeContext, metrics: RuntimeMetricsResponse) -> ReadinessResponse:
    report = validate_startup(
        settings=context.settings,
        persistence=context.persistence,
        operational=context.operational,
    )
    checks = tuple(
        ReadinessCheckResponse(
            name=check.name,
            ok=check.ok,
            severity=check.severity,
            detail=check.detail,
        )
        for check in report.checks
    )
    return ReadinessResponse(
        status="ready" if report.ok else "not_ready",
        ready=report.ok,
        checks=checks,
        metrics=metrics,
    )


def build_system_health_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
    metrics: RuntimeMetricsResponse,
) -> SystemHealthTabResponse:
    settings = context.settings
    report = validate_startup(
        settings=settings,
        persistence=context.persistence,
        operational=context.operational,
    )
    checks = tuple(
        ReadinessCheckResponse(
            name=check.name,
            ok=check.ok,
            severity=check.severity,
            detail=check.detail,
        )
        for check in report.checks
    )
    db_check = next((check for check in checks if check.name == "operational_repository_connectivity"), None)
    state = load_operator_state(
        operator_state_path(settings.storage.artifacts_dir),
        repository=context.operational.operator_control_state,
    )
    last_run = queries.last_run_summary(selector.selected_run_id)
    banners = incident_banners(
        metrics=metrics,
        operator_paused=state.paused,
        operator_pause_reason=state.pause_reason,
        last_run_status=last_run.status,
    )
    incidents_feed = queries.incident_events(
        run_id=selector.selected_run_id or None,
        limit=20,
    )

    has_startup_error = any(check.severity == "error" and not check.ok for check in checks)
    has_critical_runtime_issue = (
        metrics.tx_failed_count > 0
        or (db_check is not None and not db_check.ok)
    )
    has_operator_attention_item = (
        metrics.live_source_failures_total > 0
        or metrics.review_queue_depth > 0
        or metrics.pending_settlements_count > 0
        or metrics.tx_pending_count > 0
        or metrics.stale_data_events_total > 0
        or metrics.stale_data_blocked_trades_total > 0
        or state.paused
    )
    if has_startup_error or has_critical_runtime_issue:
        overall_status = "critical"
    elif has_operator_attention_item:
        overall_status = "warning"
    else:
        overall_status = "ok"

    return SystemHealthTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        overall_status=overall_status,
        startup_validation=StartupValidationSummaryResponse(
            ok=report.ok,
            error_count=sum(1 for check in checks if check.severity == "error" and not check.ok),
            warning_count=sum(1 for check in checks if check.severity == "warning"),
            info_count=sum(1 for check in checks if check.severity == "info"),
            checks=checks,
        ),
        healthcheck=HealthcheckSummaryResponse(
            status="ok" if report.ok else "failed",
            runtime_mode=settings.runtime.mode.value,
            execution_mode=settings.execution.mode.value,
            timestamp_utc=datetime.now(UTC).isoformat(),
            metrics=metrics,
        ),
        db_connectivity=DbConnectivitySummaryResponse(
            status="ok" if db_check is not None and db_check.ok else "failed",
            detail=db_check.detail if db_check is not None else "operational_repository_connectivity_check_missing",
            check_name=db_check.name if db_check is not None else "operational_repository_connectivity",
        ),
        provider_status=provider_status(settings, metrics),
        review_queue_depth=metrics.review_queue_depth,
        tx_pending_count=metrics.tx_pending_count,
        tx_mined_count=metrics.tx_mined_count,
        tx_failed_count=metrics.tx_failed_count,
        pending_settlements_count=metrics.pending_settlements_count,
        stale_data_events_total=metrics.stale_data_events_total,
        stale_data_blocked_trades_total=metrics.stale_data_blocked_trades_total,
        incident_banners=banners,
        incidents_feed=incidents_feed,
    )
