from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.services import OperatorControlState
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    DriftAlertResponse,
    ModelVisibilityResponse,
    OverviewTabResponse,
    RunSelectorResponse,
    RuntimeMetricsResponse,
)

from .context import UiRuntimeContext
from .positions import open_positions_count
from .queries import UiReadQueryService
from .shared import incident_banners


def build_overview_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
    metrics: RuntimeMetricsResponse,
    state: OperatorControlState,
    model_visibility: ModelVisibilityResponse,
    drift_alert: DriftAlertResponse | None,
) -> OverviewTabResponse:
    settings = context.settings
    summary = queries.last_run_summary(selector.selected_run_id)
    banners = incident_banners(
        metrics=metrics,
        operator_paused=state.paused,
        operator_pause_reason=state.pause_reason,
        last_run_status=summary.status,
    )
    incidents_feed = queries.incident_events(
        run_id=selector.selected_run_id or None,
        limit=20,
    )
    return OverviewTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        runtime_mode=settings.runtime.mode.value,
        execution_mode=settings.execution.mode.value,
        operator_paused=state.paused,
        operator_pause_reason=state.pause_reason,
        last_run=summary,
        review_queue_depth=metrics.review_queue_depth,
        open_positions_count=open_positions_count(metrics),
        pending_settlements_count=metrics.pending_settlements_count,
        live_source_failures_total=metrics.live_source_failures_total,
        tx_pending_count=metrics.tx_pending_count,
        tx_mined_count=metrics.tx_mined_count,
        tx_failed_count=metrics.tx_failed_count,
        stale_data_events_total=metrics.stale_data_events_total,
        stale_data_blocked_trades_total=metrics.stale_data_blocked_trades_total,
        model_visibility=model_visibility,
        drift_alert=drift_alert,
        counters_chart=(
            ChartPointResponse(label="live_source_failures", value=float(metrics.live_source_failures_total)),
            ChartPointResponse(label="review_queue_depth", value=float(metrics.review_queue_depth)),
            ChartPointResponse(label="open_positions", value=float(open_positions_count(metrics))),
            ChartPointResponse(label="pending_settlements", value=float(metrics.pending_settlements_count)),
            ChartPointResponse(label="tx_pending", value=float(metrics.tx_pending_count)),
            ChartPointResponse(label="tx_mined", value=float(metrics.tx_mined_count)),
            ChartPointResponse(label="tx_failed", value=float(metrics.tx_failed_count)),
            ChartPointResponse(label="stale_data_events", value=float(metrics.stale_data_events_total)),
            ChartPointResponse(
                label="stale_data_blocked_trades",
                value=float(metrics.stale_data_blocked_trades_total),
            ),
        ),
        incident_banners=banners,
        incidents_feed=incidents_feed,
    )
