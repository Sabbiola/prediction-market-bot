from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Any, Callable, TypeVar, cast

from prediction_market_bot.services import RuntimeMetricsSnapshot, load_operator_state, operator_state_path
from prediction_market_bot.ui.models import (
    ExecutionTabResponse,
    HealthResponse,
    IncidentsFeedResponse,
    OverviewTabResponse,
    PredictionTabResponse,
    PositionsTabResponse,
    ReadinessResponse,
    ReportsTabResponse,
    ResearchTabResponse,
    ReviewQueueTabResponse,
    RiskTabResponse,
    RunSelectorResponse,
    SandboxTxTabResponse,
    ScannerTabResponse,
    SettlementTabResponse,
    SystemHealthTabResponse,
)

from .context import UiRuntimeContext
from .execution import build_execution_tab
from .overview import build_overview_tab
from .positions import build_positions_tab
from .prediction import PredictionSupportService, build_prediction_tab
from .queries import UiReadQueryService
from .reports import build_reports_tab
from .research import build_research_tab
from .review_queue import build_review_queue_tab
from .risk import build_risk_tab
from .sandbox_tx import build_sandbox_tx_tab
from .scanner import build_scanner_tab
from .settlement import build_settlement_tab
from .shared import to_metrics_response
from .system import build_health, build_readiness, build_system_health_tab

_T = TypeVar("_T")
logger = logging.getLogger(__name__)


class UiReadModelService:
    def __init__(self, context: UiRuntimeContext) -> None:
        self._context = context
        self._queries = UiReadQueryService(context)
        self._prediction = PredictionSupportService(context, self._queries)
        self._poll_cache_ttl_sec = max(context.settings.performance.ui_poll_cache_ttl_sec, 0.0)
        self._slow_panel_threshold_ms = max(context.settings.performance.slow_stage_threshold_ms, 0.0)
        self._poll_cache: dict[str, tuple[float, object]] = {}

    def invalidate_poll_cache(self) -> None:
        self._poll_cache.clear()
        self._queries.clear_cache()
        self._prediction.clear_cache()

    def health(self) -> HealthResponse:
        return build_health(context=self._context)

    def readiness(self) -> ReadinessResponse:
        metrics = to_metrics_response(self._collect_runtime_metrics_snapshot())
        return build_readiness(context=self._context, metrics=metrics)

    def incidents_feed(self, *, run_id: str | None = None, limit: int = 30) -> IncidentsFeedResponse:
        run_token = run_id or ""

        def _load() -> IncidentsFeedResponse:
            rows = self._queries.incident_events(run_id=run_id, limit=limit)
            return IncidentsFeedResponse(
                generated_at=datetime.now(UTC).isoformat(),
                run_id=run_token,
                rows=rows,
            )

        return self._load_panel_with_cache(
            panel_name="incidents",
            run_id=run_token,
            loader=_load,
            limit=str(limit),
        )

    def run_selector(self, run_id: str | None = None) -> RunSelectorResponse:
        run_token = run_id or ""
        return self._load_panel_with_cache(
            panel_name="run_selector",
            run_id=run_token,
            loader=lambda: self._queries.run_selector(run_id),
        )

    def overview_tab(self, run_id: str | None = None) -> OverviewTabResponse:
        run_token = run_id or ""

        def _load() -> OverviewTabResponse:
            selector = self.run_selector(run_id)
            state = load_operator_state(
                operator_state_path(self._context.settings.storage.artifacts_dir),
                repository=self._context.operational.operator_control_state,
            )
            model_visibility = self._prediction.model_visibility(state=state)
            drift_alert = self._prediction.drift_alert(run_id=selector.selected_run_id)
            metrics = to_metrics_response(self._collect_runtime_metrics_snapshot())
            return build_overview_tab(
                context=self._context,
                queries=self._queries,
                selector=selector,
                metrics=metrics,
                state=state,
                model_visibility=model_visibility,
                drift_alert=drift_alert,
            )

        return self._load_panel_with_cache(panel_name="overview", run_id=run_token, loader=_load)

    def system_health_tab(self, run_id: str | None = None) -> SystemHealthTabResponse:
        run_token = run_id or ""

        def _load() -> SystemHealthTabResponse:
            selector = self.run_selector(run_id)
            metrics = to_metrics_response(self._collect_runtime_metrics_snapshot())
            return build_system_health_tab(
                context=self._context,
                queries=self._queries,
                selector=selector,
                metrics=metrics,
            )

        return self._load_panel_with_cache(panel_name="system", run_id=run_token, loader=_load)

    def review_queue_tab(
        self,
        run_id: str | None = None,
        *,
        status: str | None = None,
        queue_id: str | None = None,
        limit: int = 100,
    ) -> ReviewQueueTabResponse:
        run_token = run_id or ""

        def _load() -> ReviewQueueTabResponse:
            selector = self.run_selector(run_id)
            return build_review_queue_tab(
                context=self._context,
                queries=self._queries,
                selector=selector,
                status=status,
                queue_id=queue_id,
                limit=limit,
            )

        return self._load_panel_with_cache(
            panel_name="review_queue",
            run_id=run_token,
            loader=_load,
            status=status or "",
            queue_id=queue_id or "",
            limit=str(limit),
        )

    def scanner_tab(self, run_id: str | None = None) -> ScannerTabResponse:
        run_token = run_id or ""

        def _load() -> ScannerTabResponse:
            selector = self.run_selector(run_id)
            return build_scanner_tab(context=self._context, queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="scanner", run_id=run_token, loader=_load)

    def research_tab(self, run_id: str | None = None) -> ResearchTabResponse:
        run_token = run_id or ""

        def _load() -> ResearchTabResponse:
            selector = self.run_selector(run_id)
            return build_research_tab(queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="research", run_id=run_token, loader=_load)

    def prediction_tab(self, run_id: str | None = None) -> PredictionTabResponse:
        run_token = run_id or ""

        def _load() -> PredictionTabResponse:
            selector = self.run_selector(run_id)
            return build_prediction_tab(
                context=self._context,
                queries=self._queries,
                support=self._prediction,
                selector=selector,
            )

        return self._load_panel_with_cache(panel_name="prediction", run_id=run_token, loader=_load)

    def risk_tab(self, run_id: str | None = None) -> RiskTabResponse:
        run_token = run_id or ""

        def _load() -> RiskTabResponse:
            selector = self.run_selector(run_id)
            return build_risk_tab(context=self._context, queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="risk", run_id=run_token, loader=_load)

    def execution_tab(self, run_id: str | None = None) -> ExecutionTabResponse:
        run_token = run_id or ""

        def _load() -> ExecutionTabResponse:
            selector = self.run_selector(run_id)
            return build_execution_tab(context=self._context, queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="execution", run_id=run_token, loader=_load)

    def positions_tab(self, run_id: str | None = None, *, market_id: str | None = None) -> PositionsTabResponse:
        run_token = run_id or ""

        def _load() -> PositionsTabResponse:
            selector = self.run_selector(run_id)
            return build_positions_tab(
                context=self._context,
                queries=self._queries,
                selector=selector,
                market_id_filter=market_id,
            )

        return self._load_panel_with_cache(
            panel_name="positions",
            run_id=run_token,
            loader=_load,
            market_id=market_id or "",
        )

    def settlement_tab(self, run_id: str | None = None) -> SettlementTabResponse:
        run_token = run_id or ""

        def _load() -> SettlementTabResponse:
            selector = self.run_selector(run_id)
            return build_settlement_tab(context=self._context, queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="settlement", run_id=run_token, loader=_load)

    def sandbox_tx_tab(
        self,
        run_id: str | None = None,
        *,
        intent_id: str | None = None,
        limit: int = 100,
    ) -> SandboxTxTabResponse:
        run_token = run_id or ""
        _ = limit  # preserved for backward-compatible signature

        def _load() -> SandboxTxTabResponse:
            selector = self.run_selector(run_id)
            return build_sandbox_tx_tab(
                context=self._context,
                queries=self._queries,
                selector=selector,
                intent_id_filter=intent_id,
            )

        return self._load_panel_with_cache(
            panel_name="sandbox_tx",
            run_id=run_token,
            loader=_load,
            intent_id=intent_id or "",
        )

    def reports_tab(self, run_id: str | None = None) -> ReportsTabResponse:
        run_token = run_id or ""

        def _load() -> ReportsTabResponse:
            selector = self.run_selector(run_id)
            return build_reports_tab(context=self._context, queries=self._queries, selector=selector)

        return self._load_panel_with_cache(panel_name="reports", run_id=run_token, loader=_load)

    def persistence_rows(self, artifact_type: str) -> list[dict[str, Any]]:
        return self._queries.persistence_rows(artifact_type)

    def _load_panel_with_cache(self, panel_name: str, run_id: str, loader: Callable[[], _T], **parts: str) -> _T:
        cache_key = self._poll_cache_key(panel_name, run_id=run_id, **parts)
        started = perf_counter()
        payload, cache_state = self._cached_poll(cache_key, loader)
        duration_ms = max((perf_counter() - started) * 1000.0, 0.0)
        self._record_panel_timing(
            panel_name=panel_name,
            run_id=run_id,
            cache_state=cache_state,
            duration_ms=duration_ms,
        )
        return payload

    def _record_panel_timing(
        self,
        *,
        panel_name: str,
        run_id: str,
        cache_state: str,
        duration_ms: float,
    ) -> None:
        if self._slow_panel_threshold_ms > 0 and duration_ms >= self._slow_panel_threshold_ms:
            logger.warning(
                "ui_panel_slow",
                extra={
                    "event": "ui_panel_slow",
                    "panel": panel_name,
                    "run_id": run_id,
                    "cache_state": cache_state,
                    "duration_ms": round(duration_ms, 3),
                    "threshold_ms": round(self._slow_panel_threshold_ms, 3),
                },
            )
            return
        if cache_state != "hit" and duration_ms >= 75:
            logger.info(
                "ui_panel_timing",
                extra={
                    "event": "ui_panel_timing",
                    "panel": panel_name,
                    "run_id": run_id,
                    "cache_state": cache_state,
                    "duration_ms": round(duration_ms, 3),
                },
            )

    def _poll_cache_key(self, name: str, **parts: str) -> str:
        tokens = [name.strip().lower()]
        for key in sorted(parts):
            tokens.append(f"{key.strip().lower()}={parts[key].strip()}")
        return "|".join(tokens)

    def _cached_poll(self, cache_key: str, loader: Callable[[], _T]) -> tuple[_T, str]:
        if self._poll_cache_ttl_sec <= 0:
            return loader(), "disabled"
        now = monotonic()
        cached = self._poll_cache.get(cache_key)
        if cached is not None and cached[0] >= now:
            return cast(_T, cached[1]), "hit"
        payload = loader()
        self._poll_cache[cache_key] = (now + self._poll_cache_ttl_sec, payload)
        return payload, "miss"

    def _collect_runtime_metrics_snapshot(self) -> RuntimeMetricsSnapshot:
        import prediction_market_bot.ui.read_models as read_models_module

        return read_models_module.collect_runtime_metrics(
            settings=self._context.settings,
            persistence=self._context.persistence,
            operational=self._context.operational,
        )
