from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.domain.enums import TradeReviewStatus, TxConfirmationStatus
from prediction_market_bot.domain.models import TradeReviewItem, TxAttempt, TxIntent
from prediction_market_bot.infrastructure.operational_sqlite import OperationalRepositories
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.services import (
    RuntimeMetricsSnapshot,
    TradeReviewQueueService,
    collect_runtime_metrics,
    list_run_ids,
    load_operator_state,
    operator_state_path,
    replay_run,
    validate_startup,
)

from .models import (
    ChartPointResponse,
    DbConnectivitySummaryResponse,
    ExecutionRowResponse,
    ExecutionTabResponse,
    HealthResponse,
    HealthcheckSummaryResponse,
    IncidentBannerResponse,
    IncidentEventResponse,
    IncidentsFeedResponse,
    LastRunSummaryResponse,
    OverviewTabResponse,
    PredictionRowResponse,
    PredictionTabResponse,
    ProviderStatusSummaryResponse,
    ReportShortcutResponse,
    ReadinessCheckResponse,
    ReadinessResponse,
    ReviewQueueDetailResponse,
    ReviewQueueRowResponse,
    ReviewQueueTabResponse,
    ReportsTabResponse,
    ResearchPacketRowResponse,
    ResearchTabResponse,
    RiskRowResponse,
    RiskTabResponse,
    RunOptionResponse,
    RunSelectorResponse,
    RuntimeMetricsResponse,
    SandboxTxAttemptTimelineRowResponse,
    SandboxTxIntentDetailResponse,
    SandboxTxReceiptTimelineRowResponse,
    SandboxTxRowResponse,
    SandboxTxTabResponse,
    ScannerCandidateRowResponse,
    ScannerTabResponse,
    SettlementRowResponse,
    SettlementTabResponse,
    StartupValidationSummaryResponse,
    SystemHealthTabResponse,
    UiActionLogRowResponse,
)

_T = TypeVar("_T")


@dataclass(slots=True, frozen=True)
class UiRuntimeContext:
    settings: AppSettings
    persistence: JsonlPersistence
    operational: OperationalRepositories
    config_path: str
    agents_config_path: str


def build_ui_runtime_context(config_path: str, agents_config_path: str) -> UiRuntimeContext:
    settings = load_settings(config_path, agents_config_path)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    return UiRuntimeContext(
        settings=settings,
        persistence=persistence,
        operational=operational,
        config_path=config_path,
        agents_config_path=agents_config_path,
    )


class UiReadModelService:
    def __init__(self, context: UiRuntimeContext) -> None:
        self._context = context
        self._poll_cache_ttl_sec = max(context.settings.performance.ui_poll_cache_ttl_sec, 0.0)
        self._poll_cache: dict[str, tuple[float, object]] = {}

    def invalidate_poll_cache(self) -> None:
        self._poll_cache.clear()

    def health(self) -> HealthResponse:
        settings = self._context.settings
        return HealthResponse(
            status="ok",
            app_name=settings.runtime.app_name,
            environment=settings.runtime.env,
            runtime_mode=settings.runtime.mode.value,
            execution_mode=settings.execution.mode.value,
            timestamp_utc=datetime.now(UTC).isoformat(),
        )

    def readiness(self) -> ReadinessResponse:
        report = validate_startup(
            settings=self._context.settings,
            persistence=self._context.persistence,
            operational=self._context.operational,
        )
        metrics = collect_runtime_metrics(
            settings=self._context.settings,
            persistence=self._context.persistence,
            operational=self._context.operational,
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
            metrics=_to_metrics_response(metrics),
        )

    def incidents_feed(self, *, run_id: str | None = None, limit: int = 30) -> IncidentsFeedResponse:
        cache_key = self._poll_cache_key("incidents", run_id=run_id or "", limit=str(limit))

        def _load() -> IncidentsFeedResponse:
            rows = self._incident_events(run_id=run_id, limit=limit)
            return IncidentsFeedResponse(
                generated_at=datetime.now(UTC).isoformat(),
                run_id=run_id or "",
                rows=rows,
            )

        return self._cached_poll(cache_key, _load)

    def run_selector(self, run_id: str | None = None) -> RunSelectorResponse:
        run_ids = list_run_ids(self._context.persistence, limit_runs=500)
        latest_run_id = run_ids[-1] if run_ids else ""
        requested_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else ""
        selected_run_id = requested_run_id or latest_run_id
        options_list = [RunOptionResponse(run_id=item, label=item) for item in reversed(run_ids[-100:])]
        if requested_run_id and requested_run_id not in run_ids:
            options_list.insert(0, RunOptionResponse(run_id=requested_run_id, label=f"{requested_run_id} (external)"))
        options = tuple(options_list)
        available = bool(run_ids) or bool(requested_run_id)
        if not available:
            note = "no_runs_available"
        elif requested_run_id and requested_run_id not in run_ids:
            note = "run_selector_external_run_id"
        else:
            note = "run_selector_ready"
        return RunSelectorResponse(
            selected_run_id=selected_run_id,
            latest_run_id=latest_run_id,
            options=options,
            available=available,
            note=note,
        )

    def _poll_cache_key(self, name: str, **parts: str) -> str:
        tokens = [name.strip().lower()]
        for key in sorted(parts):
            tokens.append(f"{key.strip().lower()}={parts[key].strip()}")
        return "|".join(tokens)

    def _cached_poll(self, cache_key: str, loader: Callable[[], _T]) -> _T:
        if self._poll_cache_ttl_sec <= 0:
            return loader()
        now = monotonic()
        cached = self._poll_cache.get(cache_key)
        if cached is not None and cached[0] >= now:
            return cast(_T, cached[1])
        payload = loader()
        self._poll_cache[cache_key] = (now + self._poll_cache_ttl_sec, payload)
        return payload

    def overview_tab(self, run_id: str | None = None) -> OverviewTabResponse:
        cache_key = self._poll_cache_key("overview", run_id=run_id or "")

        def _load() -> OverviewTabResponse:
            settings = self._context.settings
            selector = self.run_selector(run_id)
            state = load_operator_state(
                operator_state_path(settings.storage.artifacts_dir),
                repository=self._context.operational.operator_control_state,
            )
            metrics = _to_metrics_response(
                collect_runtime_metrics(
                    settings=settings,
                    persistence=self._context.persistence,
                    operational=self._context.operational,
                )
            )
            summary = self._last_run_summary(selector.selected_run_id)
            incident_banners = _incident_banners(
                metrics=metrics,
                operator_paused=state.paused,
                operator_pause_reason=state.pause_reason,
                last_run_status=summary.status,
            )
            incidents_feed = self._incident_events(
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
                open_positions_count=metrics.open_positions_count,
                pending_settlements_count=metrics.pending_settlements_count,
                live_source_failures_total=metrics.live_source_failures_total,
                tx_pending_count=metrics.tx_pending_count,
                tx_mined_count=metrics.tx_mined_count,
                tx_failed_count=metrics.tx_failed_count,
                stale_data_events_total=metrics.stale_data_events_total,
                stale_data_blocked_trades_total=metrics.stale_data_blocked_trades_total,
                counters_chart=(
                    ChartPointResponse(label="live_source_failures", value=float(metrics.live_source_failures_total)),
                    ChartPointResponse(label="review_queue_depth", value=float(metrics.review_queue_depth)),
                    ChartPointResponse(label="open_positions", value=float(metrics.open_positions_count)),
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
                incident_banners=incident_banners,
                incidents_feed=incidents_feed,
            )

        return self._cached_poll(cache_key, _load)

    def system_health_tab(self, run_id: str | None = None) -> SystemHealthTabResponse:
        cache_key = self._poll_cache_key("system", run_id=run_id or "")

        def _load() -> SystemHealthTabResponse:
            settings = self._context.settings
            selector = self.run_selector(run_id)
            report = validate_startup(
                settings=settings,
                persistence=self._context.persistence,
                operational=self._context.operational,
            )
            metrics = _to_metrics_response(
                collect_runtime_metrics(
                    settings=settings,
                    persistence=self._context.persistence,
                    operational=self._context.operational,
                )
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
            return SystemHealthTabResponse(
                generated_at=datetime.now(UTC).isoformat(),
                run_selector=selector,
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
                provider_status=_provider_status(settings, metrics),
            )

        return self._cached_poll(cache_key, _load)

    def review_queue_tab(
        self,
        run_id: str | None = None,
        *,
        status: str | None = None,
        queue_id: str | None = None,
        limit: int = 100,
    ) -> ReviewQueueTabResponse:
        selector = self.run_selector(run_id)
        queue = TradeReviewQueueService(
            self._context.persistence,
            candidate_repo=self._context.operational.review_queue,
            decision_repo=self._context.operational.review_decisions,
        )
        active_filter = _normalize_review_filter(status) or TradeReviewStatus.PENDING_REVIEW
        all_items = queue.list_queue(run_id=selector.selected_run_id or None, status=None, limit=0)
        filtered = queue.list_queue(
            run_id=selector.selected_run_id or None,
            status=active_filter,
            limit=limit,
        )
        counts = _review_status_counts(all_items)
        rows = tuple(_review_row(item) for item in filtered)

        selected_queue_id = queue_id.strip() if isinstance(queue_id, str) else ""
        if not selected_queue_id and rows:
            selected_queue_id = rows[0].queue_id
        selected_item_raw = queue.get_item(selected_queue_id) if selected_queue_id else None
        selected_item = _review_detail(selected_item_raw) if selected_item_raw is not None else None
        logs = self._ui_action_logs(action_prefix="review-", run_id=selector.selected_run_id, limit=12)

        return ReviewQueueTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=bool(all_items),
            note="review_queue_loaded" if all_items else "review_queue_empty",
            active_status_filter=active_filter.value,
            status_filters=(
                TradeReviewStatus.PENDING_REVIEW.value,
                TradeReviewStatus.APPROVED.value,
                TradeReviewStatus.REJECTED.value,
                TradeReviewStatus.EXPIRED.value,
            ),
            pending_count=counts.get(TradeReviewStatus.PENDING_REVIEW.value, 0),
            approved_count=counts.get(TradeReviewStatus.APPROVED.value, 0),
            rejected_count=counts.get(TradeReviewStatus.REJECTED.value, 0),
            expired_count=counts.get(TradeReviewStatus.EXPIRED.value, 0),
            total_count=len(all_items),
            rows=rows,
            selected_queue_id=selected_queue_id,
            selected_item=selected_item,
            recent_action_logs=logs,
        )

    def scanner_tab(self, run_id: str | None = None) -> ScannerTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_scanner(selector, "no_run_selected")
        snapshots = self._artifact_payloads(run, "market_snapshots")
        candidates = self._artifact_payloads(run, "market_candidates")
        rows: list[ScannerCandidateRowResponse] = []
        reason_counter: dict[str, int] = {}
        score_sum = 0.0
        for payload in candidates:
            snapshot = _as_map(payload.get("market"))
            market_id = _extract_market_id(payload) or _extract_market_id(snapshot)
            if not market_id:
                continue
            reasons = _to_text_tuple(payload.get("reasons"))
            for reason in reasons:
                reason_counter[reason] = reason_counter.get(reason, 0) + 1
            scan_score = _to_float(payload.get("scan_score"))
            score_sum += scan_score
            rows.append(
                ScannerCandidateRowResponse(
                    market_id=market_id,
                    title=_to_text(_deep_get(snapshot, "market", "title")) or "n/a",
                    category=_to_text(_deep_get(snapshot, "market", "category")) or "n/a",
                    scan_score=scan_score,
                    reasons=reasons,
                    liquidity_usd=_to_float(snapshot.get("liquidity_usd")),
                    volume_24h_usd=_to_float(snapshot.get("volume_24h_usd")),
                    spread_bps=_to_int(snapshot.get("spread_bps")),
                    hours_to_resolution=_to_float(snapshot.get("hours_to_resolution")),
                )
            )
        rows.sort(key=lambda item: item.scan_score, reverse=True)
        count = len(rows)
        return ScannerTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=count > 0 or len(snapshots) > 0,
            note="scanner_data_loaded" if count > 0 else "scanner_data_missing",
            total_markets=len(snapshots),
            candidates_count=count,
            avg_scan_score=round(score_sum / count, 4) if count > 0 else 0.0,
            top_reasons=_chart_from_counter(reason_counter),
            candidates=tuple(rows[:30]),
        )

    def research_tab(self, run_id: str | None = None) -> ResearchTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_research(selector, "no_run_selected")
        packets = self._artifact_payloads(run, "research_packets")
        rows: list[ResearchPacketRowResponse] = []
        source_counter: dict[str, int] = {}
        findings_total = 0
        evidence_sum = 0.0
        disagreement_sum = 0.0
        for payload in packets:
            findings = _as_sequence(payload.get("findings"))
            source_types = sorted({_to_text(item.get("source_type")) for item in findings if isinstance(item, Mapping)})
            for source in source_types:
                if source:
                    source_counter[source] = source_counter.get(source, 0) + 1
            findings_count = len(findings)
            evidence = _to_float(payload.get("evidence_strength"))
            disagreement = _to_float(payload.get("disagreement_score"))
            rows.append(
                ResearchPacketRowResponse(
                    market_id=_to_text(payload.get("market_id")) or "n/a",
                    findings_count=findings_count,
                    evidence_strength=evidence,
                    disagreement_score=disagreement,
                    weighted_sentiment=_to_float(payload.get("weighted_sentiment")),
                    source_types=tuple(source_types),
                    narrative_summary=_to_text(payload.get("narrative_summary")),
                )
            )
            findings_total += findings_count
            evidence_sum += evidence
            disagreement_sum += disagreement
        rows.sort(key=lambda item: item.evidence_strength, reverse=True)
        count = len(rows)
        return ResearchTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=count > 0,
            note="research_data_loaded" if count > 0 else "research_data_missing",
            packets_count=count,
            findings_count=findings_total,
            avg_evidence_strength=round(evidence_sum / count, 4) if count > 0 else 0.0,
            avg_disagreement_score=round(disagreement_sum / count, 4) if count > 0 else 0.0,
            source_type_distribution=_chart_from_counter(source_counter),
            packets=tuple(rows[:30]),
        )

    def prediction_tab(self, run_id: str | None = None) -> PredictionTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_prediction(selector, "no_run_selected")
        predictions = self._artifact_payloads(run, "prediction_results")
        rows: list[PredictionRowResponse] = []
        side_counter: dict[str, int] = {}
        confidence_sum = 0.0
        edge_sum_bps = 0.0
        for payload in predictions:
            side = _to_text(payload.get("selected_side")) or "n/a"
            confidence = _to_float(payload.get("confidence"))
            edge_bps = _to_float(payload.get("edge")) * 10_000.0
            side_counter[side] = side_counter.get(side, 0) + 1
            confidence_sum += confidence
            edge_sum_bps += edge_bps
            rows.append(
                PredictionRowResponse(
                    market_id=_to_text(payload.get("market_id")) or "n/a",
                    selected_side=side,
                    market_yes_prob=_to_float(payload.get("market_yes_prob")),
                    fair_yes_prob=_to_float(payload.get("fair_yes_prob")),
                    edge_bps=round(edge_bps, 2),
                    confidence=confidence,
                    rationale=_to_text_tuple(payload.get("rationale")),
                )
            )
        rows.sort(key=lambda item: abs(item.edge_bps), reverse=True)
        count = len(rows)
        return PredictionTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=count > 0,
            note="prediction_data_loaded" if count > 0 else "prediction_data_missing",
            predictions_count=count,
            avg_confidence=round(confidence_sum / count, 4) if count > 0 else 0.0,
            avg_edge_bps=round(edge_sum_bps / count, 2) if count > 0 else 0.0,
            side_distribution=_chart_from_counter(side_counter),
            rows=tuple(rows[:30]),
        )

    def risk_tab(self, run_id: str | None = None) -> RiskTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_risk(selector, "no_run_selected")
        decisions = self._artifact_payloads(run, "effective_risk_decisions")
        if not decisions:
            decisions = self._artifact_payloads(run, "risk_decisions")
        rows: list[RiskRowResponse] = []
        reason_counter: dict[str, int] = {}
        approved_count = 0
        stake_sum = 0.0
        for payload in decisions:
            approved = bool(payload.get("approved", False))
            reasoning = _to_text_tuple(payload.get("reasoning"))
            if approved:
                approved_count += 1
            else:
                for reason in reasoning:
                    token = reason.split(" ", 1)[0].strip()
                    if token and token != "approved":
                        reason_counter[token] = reason_counter.get(token, 0) + 1
            stake = _to_float(payload.get("stake_usd"))
            stake_sum += stake
            rows.append(
                RiskRowResponse(
                    market_id=_to_text(payload.get("market_id")) or "n/a",
                    approved=approved,
                    side=_to_text(payload.get("side")) or "n/a",
                    stake_usd=stake,
                    bankroll_fraction=_to_float(payload.get("bankroll_fraction")),
                    fractional_kelly=_to_float(payload.get("fractional_kelly")),
                    reasoning=reasoning,
                )
            )
        rows.sort(key=lambda item: item.stake_usd, reverse=True)
        count = len(rows)
        return RiskTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=count > 0,
            note="risk_data_loaded" if count > 0 else "risk_data_missing",
            decisions_count=count,
            approved_count=approved_count,
            blocked_count=max(count - approved_count, 0),
            avg_stake_usd=round(stake_sum / count, 2) if count > 0 else 0.0,
            reason_code_distribution=_chart_from_counter(reason_counter),
            rows=tuple(rows[:30]),
        )

    def execution_tab(self, run_id: str | None = None) -> ExecutionTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_execution(selector, "no_run_selected")
        executions = self._artifact_payloads(run, "execution_results")
        intents = self._artifact_payloads(run, "order_intents")
        intent_by_market: dict[str, Mapping[str, Any]] = {}
        for payload in intents:
            market_id = _to_text(payload.get("market_id"))
            if market_id:
                intent_by_market[market_id] = payload
        status_counter: dict[str, int] = {}
        rows: list[ExecutionRowResponse] = []
        for payload in executions:
            market_id = _to_text(payload.get("market_id"))
            status = _to_text(payload.get("status")) or "UNKNOWN"
            status_counter[status] = status_counter.get(status, 0) + 1
            intent = intent_by_market.get(market_id, {})
            rows.append(
                ExecutionRowResponse(
                    market_id=market_id or "n/a",
                    status=status,
                    side=_to_text(payload.get("side")) or "n/a",
                    stake_usd=_to_float(payload.get("stake_usd")),
                    fill_price=_to_optional_float(payload.get("fill_price")),
                    order_id=_to_text(payload.get("order_id")),
                    execution_mode=_to_text(payload.get("execution_mode")),
                    intent_rationale=_to_text(intent.get("rationale")),
                    message=_to_text(payload.get("message")),
                )
            )
        rows.sort(key=lambda item: item.stake_usd, reverse=True)
        return ExecutionTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=len(rows) > 0,
            note="execution_data_loaded" if rows else "execution_data_missing",
            intents_count=len(intent_by_market),
            executions_count=len(rows),
            status_distribution=_chart_from_counter(status_counter),
            rows=tuple(rows[:30]),
        )

    def settlement_tab(self, run_id: str | None = None) -> SettlementTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_settlement(selector, "no_run_selected")
        settlements = self._artifact_payloads(run, "settlement_results")
        pending = self._artifact_payloads(run, "pending_settlement_requests")
        resolution_checks = self._artifact_payloads(run, "resolution_checks")
        reason_by_market: dict[str, str] = {}
        for payload in resolution_checks:
            market_id = _to_text(payload.get("market_id"))
            if market_id:
                reason_by_market[market_id] = _to_text(payload.get("reason"))
        outcome_counter: dict[str, int] = {}
        rows: list[SettlementRowResponse] = []
        for payload in settlements:
            market_id = _to_text(payload.get("market_id"))
            outcome = _to_text(payload.get("outcome_classification")) or "UNKNOWN"
            outcome_counter[outcome] = outcome_counter.get(outcome, 0) + 1
            resolved_raw = payload.get("resolved_yes")
            resolved_yes = "n/a"
            if isinstance(resolved_raw, bool):
                resolved_yes = "YES" if resolved_raw else "NO"
            rows.append(
                SettlementRowResponse(
                    market_id=market_id or "n/a",
                    outcome_classification=outcome,
                    resolved_yes=resolved_yes,
                    pnl_usd=_to_float(payload.get("pnl_usd")),
                    execution_side=_to_text(payload.get("side")) or "n/a",
                    resolution_reason=reason_by_market.get(market_id, ""),
                )
            )
        rows.sort(key=lambda item: abs(item.pnl_usd), reverse=True)
        pending_count = sum(1 for payload in pending if _to_text(payload.get("state")) == "PENDING")
        available = bool(rows) or pending_count > 0 or bool(resolution_checks)
        return SettlementTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=available,
            note="settlement_data_loaded" if available else "settlement_data_missing",
            pending_requests_count=pending_count,
            resolution_checks_count=len(resolution_checks),
            settled_count=len(rows),
            outcome_distribution=_chart_from_counter(outcome_counter),
            rows=tuple(rows[:30]),
        )

    def sandbox_tx_tab(
        self,
        run_id: str | None = None,
        *,
        intent_id: str | None = None,
        limit: int = 100,
    ) -> SandboxTxTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_sandbox_tx(selector, "no_run_selected")
        attempts = self._context.operational.transaction_attempts.list_attempts(run_id=run, limit=0)
        receipts = self._context.operational.transaction_receipts.list_receipts(run_id=run, limit=0)
        if not attempts:
            attempts = self._attempts_from_artifacts(run)
        intents = self._context.operational.transaction_intents.list_intents(run_id=run, limit=0)
        counter: dict[str, int] = {}
        rows: list[SandboxTxRowResponse] = []
        latest_by_intent: dict[str, TxAttempt] = {}
        for attempt in attempts:
            status = attempt.confirmation_status.value
            counter[status] = counter.get(status, 0) + 1
            previous = latest_by_intent.get(attempt.intent_id)
            if previous is None or _attempt_sort_key(attempt) > _attempt_sort_key(previous):
                latest_by_intent[attempt.intent_id] = attempt
            rows.append(
                SandboxTxRowResponse(
                    intent_id=attempt.intent_id,
                    market_id=attempt.market_id,
                    side=attempt.side.value,
                    execution_mode=attempt.execution_mode.value,
                    confirmation_status=status,
                    tx_hash=attempt.tx_hash or "",
                    nonce=attempt.nonce,
                    retry_count=attempt.retry_count,
                    replacement_for_tx_hash=attempt.replacement_for_tx_hash,
                    replaced_by_tx_hash=attempt.replaced_by_tx_hash,
                    message=attempt.message,
                )
            )
        rows.sort(key=lambda item: _status_priority(item.confirmation_status), reverse=True)
        selected_intent_id = intent_id.strip() if isinstance(intent_id, str) else ""
        if not selected_intent_id and rows:
            selected_intent_id = rows[0].intent_id
        selected_intent_raw = next((item for item in intents if _intent_id(item) == selected_intent_id), None)
        selected_intent = (
            SandboxTxIntentDetailResponse(
                intent_id=_intent_id(selected_intent_raw),
                run_id=selected_intent_raw.run_id,
                market_id=selected_intent_raw.market_id,
                review_queue_id=selected_intent_raw.review_queue_id,
                venue=selected_intent_raw.venue,
                side=selected_intent_raw.side.value,
                stake_usd=selected_intent_raw.stake_usd,
                limit_price=selected_intent_raw.limit_price,
                rationale=selected_intent_raw.rationale,
            )
            if selected_intent_raw is not None
            else None
        )
        attempt_timeline = tuple(
            SandboxTxAttemptTimelineRowResponse(
                submitted_at=item.submitted_at.isoformat() if item.submitted_at else "",
                confirmation_status=item.confirmation_status.value,
                status=item.status.value,
                tx_hash=item.tx_hash or "",
                nonce=item.nonce,
                retry_count=item.retry_count,
                replacement_for_tx_hash=item.replacement_for_tx_hash,
                replaced_by_tx_hash=item.replaced_by_tx_hash,
                message=item.message,
            )
            for item in sorted(
                [row for row in attempts if row.intent_id == selected_intent_id],
                key=_attempt_sort_key,
            )
        )
        receipt_timeline = tuple(
            SandboxTxReceiptTimelineRowResponse(
                submitted_at=item.submitted_at.isoformat() if item.submitted_at else "",
                confirmed_at=item.confirmed_at.isoformat() if item.confirmed_at else "",
                status=item.status.value,
                confirmation_status=item.confirmation_status.value,
                tx_hash=item.tx_hash or "",
                nonce=item.nonce,
                order_id=item.order_id or "",
                message=item.message,
            )
            for item in sorted(
                [row for row in receipts if row.intent_id == selected_intent_id],
                key=lambda row: row.submitted_at or datetime.min.replace(tzinfo=UTC),
            )
        )
        selected_latest = latest_by_intent.get(selected_intent_id)
        can_resubmit = (
            selected_latest is not None
            and self._context.settings.sandbox_chain.submit_tx
            and selected_latest.confirmation_status not in {TxConfirmationStatus.PENDING, TxConfirmationStatus.MINED}
        )
        if not self._context.settings.sandbox_chain.submit_tx:
            action_hint = "resubmit_disabled_submit_tx_false"
        elif selected_latest is None:
            action_hint = "no_attempt_selected"
        elif can_resubmit:
            action_hint = "resubmit_safe_allowed"
        else:
            action_hint = "resubmit_blocked_pending_or_mined"
        logs = self._ui_action_logs(action_prefix="tx-", run_id=run, limit=12)
        return SandboxTxTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=bool(rows),
            note="sandbox_tx_data_loaded" if rows else "sandbox_tx_data_missing",
            attempts_count=len(rows),
            receipts_count=len(receipts),
            confirmation_distribution=_chart_from_counter(counter),
            rows=tuple(rows[:50]),
            active_intent_id=selected_intent_id,
            can_reconcile=bool(rows),
            can_resubmit_safe=can_resubmit,
            action_hint=action_hint,
            selected_intent=selected_intent,
            attempt_timeline=attempt_timeline,
            receipt_timeline=receipt_timeline,
            recent_action_logs=logs,
        )

    def reports_tab(self, run_id: str | None = None) -> ReportsTabResponse:
        selector = self.run_selector(run_id)
        run = selector.selected_run_id
        if not run:
            return _empty_reports(
                selector,
                "no_run_selected",
                config_path=self._context.config_path,
                agents_config_path=self._context.agents_config_path,
            )
        summary = replay_run(self._context.persistence, run)
        available = not (summary.event_count == 0 and summary.total_markets == 0 and summary.candidates == 0)
        state = load_operator_state(
            operator_state_path(self._context.settings.storage.artifacts_dir),
            repository=self._context.operational.operator_control_state,
        )
        reports_dir = Path(self._context.settings.storage.artifacts_dir) / "reports"
        md_path = reports_dir / f"{run}.md"
        json_path = reports_dir / f"{run}.json"
        report_markdown_path = str(md_path) if md_path.exists() else ""
        report_json_path = str(json_path) if json_path.exists() else ""
        if not report_markdown_path and state.last_run_id == run:
            report_markdown_path = state.last_report_markdown_path
        if not report_json_path and state.last_run_id == run:
            report_json_path = state.last_report_json_path
        replay_shortcut, generate_report_shortcut, eval_run_shortcut, eval_window_shortcut = _report_shortcuts(
            run_id=run,
            config_path=self._context.config_path,
            agents_config_path=self._context.agents_config_path,
        )
        return ReportsTabResponse(
            generated_at=datetime.now(UTC).isoformat(),
            run_selector=selector,
            available=available,
            note="report_data_loaded" if available else "report_data_missing",
            run_id=summary.run_id,
            started_at=summary.started_at.isoformat() if summary.started_at else "",
            finished_at=summary.finished_at.isoformat() if summary.finished_at else "",
            total_markets=summary.total_markets,
            candidates=summary.candidates,
            executed=summary.executed,
            settled=summary.settled,
            wins=summary.wins,
            losses=summary.losses,
            skipped=summary.skipped,
            event_count=summary.event_count,
            artifact_counts=_chart_from_mapping(summary.artifact_counts),
            stage_timings_ms=_chart_from_mapping(summary.observability.get("stage_timings_ms")),
            failure_categories=_chart_from_mapping(summary.failure_categories),
            report_markdown_path=report_markdown_path,
            report_json_path=report_json_path,
            replay_shortcut=replay_shortcut,
            generate_report_shortcut=generate_report_shortcut,
            eval_run_shortcut=eval_run_shortcut,
            eval_window_shortcut=eval_window_shortcut,
            incidents_feed=self._incident_events(run_id=run, limit=20),
        )

    def _last_run_summary(self, run_id: str) -> LastRunSummaryResponse:
        selected = run_id or self.run_selector().latest_run_id
        if not selected:
            return LastRunSummaryResponse(
                run_id="",
                status="none",
                started_at="",
                finished_at="",
                total_markets=0,
                candidates=0,
                executed=0,
                settled=0,
                wins=0,
                losses=0,
                skipped=0,
                event_count=0,
                available=False,
                note="no_run_available",
            )
        summary = replay_run(self._context.persistence, selected)
        has_data = not (summary.event_count == 0 and summary.total_markets == 0 and summary.candidates == 0)
        return LastRunSummaryResponse(
            run_id=summary.run_id,
            status="ok" if has_data else "missing",
            started_at=summary.started_at.isoformat() if summary.started_at else "",
            finished_at=summary.finished_at.isoformat() if summary.finished_at else "",
            total_markets=summary.total_markets,
            candidates=summary.candidates,
            executed=summary.executed,
            settled=summary.settled,
            wins=summary.wins,
            losses=summary.losses,
            skipped=summary.skipped,
            event_count=summary.event_count,
            available=has_data,
            note="run_summary_loaded" if has_data else "run_summary_empty",
        )

    def _artifact_payloads(self, run_id: str, artifact_type: str) -> list[dict[str, Any]]:
        if not run_id.strip():
            return []
        rows = self._context.persistence.read_artifact_records(run_id, artifact_type)
        payloads: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, Mapping):
                payloads.append(dict(payload))
        return payloads

    def _attempts_from_artifacts(self, run_id: str) -> list[TxAttempt]:
        rows = self._artifact_payloads(run_id, "transaction_attempts")
        attempts: list[TxAttempt] = []
        for payload in rows:
            try:
                attempts.append(TxAttempt.model_validate(payload, strict=False))
            except Exception:
                continue
        return attempts

    def _ui_action_logs(
        self,
        *,
        action_prefix: str,
        run_id: str | None,
        limit: int,
    ) -> tuple[UiActionLogRowResponse, ...]:
        records = self.persistence_rows("ui_operator_actions")
        rows: list[UiActionLogRowResponse] = []
        for row in records:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            action = _to_text(payload.get("action"))
            if action_prefix and not action.startswith(action_prefix):
                continue
            payload_run_id = _to_text(payload.get("run_id"))
            if run_id and payload_run_id and payload_run_id != run_id:
                continue
            rows.append(
                UiActionLogRowResponse(
                    action_id=_to_text(payload.get("action_id")),
                    action=action,
                    accepted=bool(payload.get("accepted", False)),
                    status=_to_text(payload.get("status")),
                    message=_to_text(payload.get("message")),
                    created_at=_to_text(payload.get("created_at")),
                    run_id=payload_run_id,
                    queue_id=_to_text(payload.get("queue_id")),
                    intent_id=_to_text(payload.get("intent_id")),
                    operator_id=_to_text(payload.get("operator_id")),
                    acting_user=_to_text(payload.get("acting_user")),
                    acting_role=_to_text(payload.get("acting_role")),
                )
            )
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(rows[: max(limit, 0)])

    def _incident_events(
        self,
        *,
        run_id: str | None,
        limit: int,
    ) -> tuple[IncidentEventResponse, ...]:
        events = self._context.persistence.read_all_run_events()
        filtered: list[IncidentEventResponse] = []
        for row in events:
            event_run_id = _to_text(row.get("run_id"))
            if run_id and event_run_id and event_run_id != run_id:
                continue
            event_type = _to_text(row.get("event_type"))
            payload = row.get("payload")
            payload_map = dict(payload) if isinstance(payload, Mapping) else {}
            severity = _event_severity(event_type=event_type, payload=payload_map)
            if severity == "info" and not _is_noteworthy_event(event_type=event_type, payload=payload_map):
                continue
            filtered.append(
                IncidentEventResponse(
                    timestamp=_to_text(row.get("timestamp")),
                    run_id=event_run_id,
                    event_type=event_type,
                    severity=severity,
                    summary=_event_summary(event_type=event_type, payload=payload_map),
                )
            )
        filtered.sort(key=lambda item: item.timestamp, reverse=True)
        return tuple(filtered[: max(limit, 0)])

    def persistence_rows(self, artifact_type: str) -> list[dict[str, Any]]:
        return self._context.persistence.read_all_artifact_records(artifact_type)


def _empty_scanner(selector: RunSelectorResponse, note: str) -> ScannerTabResponse:
    return ScannerTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        total_markets=0,
        candidates_count=0,
        avg_scan_score=0.0,
        top_reasons=(),
        candidates=(),
    )


def _empty_research(selector: RunSelectorResponse, note: str) -> ResearchTabResponse:
    return ResearchTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        packets_count=0,
        findings_count=0,
        avg_evidence_strength=0.0,
        avg_disagreement_score=0.0,
        source_type_distribution=(),
        packets=(),
    )


def _empty_prediction(selector: RunSelectorResponse, note: str) -> PredictionTabResponse:
    return PredictionTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        predictions_count=0,
        avg_confidence=0.0,
        avg_edge_bps=0.0,
        side_distribution=(),
        rows=(),
    )


def _empty_risk(selector: RunSelectorResponse, note: str) -> RiskTabResponse:
    return RiskTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        decisions_count=0,
        approved_count=0,
        blocked_count=0,
        avg_stake_usd=0.0,
        reason_code_distribution=(),
        rows=(),
    )


def _empty_execution(selector: RunSelectorResponse, note: str) -> ExecutionTabResponse:
    return ExecutionTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        intents_count=0,
        executions_count=0,
        status_distribution=(),
        rows=(),
    )


def _empty_settlement(selector: RunSelectorResponse, note: str) -> SettlementTabResponse:
    return SettlementTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        pending_requests_count=0,
        resolution_checks_count=0,
        settled_count=0,
        outcome_distribution=(),
        rows=(),
    )


def _empty_sandbox_tx(selector: RunSelectorResponse, note: str) -> SandboxTxTabResponse:
    return SandboxTxTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        attempts_count=0,
        receipts_count=0,
        confirmation_distribution=(),
        rows=(),
    )


def _empty_reports(
    selector: RunSelectorResponse,
    note: str,
    *,
    config_path: str,
    agents_config_path: str,
) -> ReportsTabResponse:
    replay_shortcut, generate_report_shortcut, eval_run_shortcut, eval_window_shortcut = _report_shortcuts(
        run_id="",
        config_path=config_path,
        agents_config_path=agents_config_path,
    )
    return ReportsTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        run_id="",
        started_at="",
        finished_at="",
        total_markets=0,
        candidates=0,
        executed=0,
        settled=0,
        wins=0,
        losses=0,
        skipped=0,
        event_count=0,
        artifact_counts=(),
        stage_timings_ms=(),
        failure_categories=(),
        report_markdown_path="",
        report_json_path="",
        replay_shortcut=replay_shortcut,
        generate_report_shortcut=generate_report_shortcut,
        eval_run_shortcut=eval_run_shortcut,
        eval_window_shortcut=eval_window_shortcut,
        incidents_feed=(),
    )


def _to_metrics_response(snapshot: RuntimeMetricsSnapshot) -> RuntimeMetricsResponse:
    payload = snapshot.to_dict()
    return RuntimeMetricsResponse(
        live_source_failures_total=int(payload.get("live_source_failures_total", 0)),
        review_queue_depth=int(payload.get("review_queue_depth", 0)),
        open_positions_count=int(payload.get("open_positions_count", 0)),
        pending_settlements_count=int(payload.get("pending_settlements_count", 0)),
        tx_pending_count=int(payload.get("tx_pending_count", 0)),
        tx_mined_count=int(payload.get("tx_mined_count", 0)),
        tx_failed_count=int(payload.get("tx_failed_count", 0)),
        stale_data_events_total=int(payload.get("stale_data_events_total", 0)),
        stale_data_blocked_trades_total=int(payload.get("stale_data_blocked_trades_total", 0)),
    )


def _provider_status(settings: AppSettings, metrics: RuntimeMetricsResponse) -> ProviderStatusSummaryResponse:
    return ProviderStatusSummaryResponse(
        status="degraded" if metrics.live_source_failures_total > 0 else "ok",
        market_data_provider=settings.runtime.market_data_provider.value,
        research_provider=settings.runtime.research_provider.value,
        provider_failure_policy=settings.runtime.provider_failure_policy.value,
        live_market_data_enabled=settings.live_market_data.enabled,
        live_research_enabled=settings.live_research.enabled,
        sandbox_chain_enabled=settings.sandbox_chain.enabled,
        sandbox_submit_tx=settings.sandbox_chain.submit_tx,
        live_source_failures_total=metrics.live_source_failures_total,
    )


def _incident_banners(
    *,
    metrics: RuntimeMetricsResponse,
    operator_paused: bool,
    operator_pause_reason: str,
    last_run_status: str,
) -> tuple[IncidentBannerResponse, ...]:
    rows: list[IncidentBannerResponse] = []
    if operator_paused:
        rows.append(
            IncidentBannerResponse(
                level="warning",
                code="operator_pause_active",
                title="Operator pause is active",
                detail=f"Execution loop is paused. reason='{operator_pause_reason or 'not_set'}'",
                recommendation="Resume only after incident triage is completed.",
            )
        )
    if metrics.live_source_failures_total > 0:
        rows.append(
            IncidentBannerResponse(
                level="warning",
                code="live_source_failures_detected",
                title="Live source failures detected",
                detail=f"Detected {metrics.live_source_failures_total} source failure artifacts.",
                recommendation="Check provider connectivity and fallback policy before continuing.",
            )
        )
    if metrics.tx_failed_count > 0:
        rows.append(
            IncidentBannerResponse(
                level="danger",
                code="sandbox_tx_failures_detected",
                title="Sandbox transaction failures require attention",
                detail=f"{metrics.tx_failed_count} tx intents are in failed or dropped state.",
                recommendation="Run tx-reconcile and use tx-resubmit-safe only when allowed.",
            )
        )
    if metrics.stale_data_blocked_trades_total > 0 or metrics.stale_data_events_total > 0:
        rows.append(
            IncidentBannerResponse(
                level="warning",
                code="stale_data_guardrail_triggered",
                title="Stale market data guardrail triggered",
                detail=(
                    f"stale_events={metrics.stale_data_events_total}, "
                    f"blocked_trades={metrics.stale_data_blocked_trades_total}"
                ),
                recommendation="Verify data freshness before approving or executing new intents.",
            )
        )
    if metrics.review_queue_depth > 0:
        rows.append(
            IncidentBannerResponse(
                level="info",
                code="review_queue_pending",
                title="Review queue has pending candidates",
                detail=f"pending_review_candidates={metrics.review_queue_depth}",
                recommendation="Process pending review items to unblock execution lane.",
            )
        )
    if metrics.pending_settlements_count > 0:
        rows.append(
            IncidentBannerResponse(
                level="info",
                code="pending_settlements_open",
                title="Settlement queue has open work",
                detail=f"pending_settlements={metrics.pending_settlements_count}",
                recommendation="Run settlement lane and verify resolution poller outputs.",
            )
        )
    if not rows:
        rows.append(
            IncidentBannerResponse(
                level="info",
                code="system_nominal",
                title="No active incidents",
                detail=f"Latest run status: {last_run_status}",
                recommendation="Keep monitoring overview counters and system health checks.",
            )
        )
    return tuple(rows)


def _report_shortcuts(
    *,
    run_id: str,
    config_path: str,
    agents_config_path: str,
) -> tuple[ReportShortcutResponse, ReportShortcutResponse, ReportShortcutResponse, ReportShortcutResponse]:
    target_run = run_id or "<run_id>"
    replay = ReportShortcutResponse(
        label="Replay Run",
        command=(
            "python -m prediction_market_bot.main replay-run "
            f"--config {config_path} --agents-config {agents_config_path} --run-id {target_run}"
        ),
        description="Replay one persisted run from artifacts and audit events.",
    )
    generate_report = ReportShortcutResponse(
        label="Generate Report",
        command=(
            "python -m prediction_market_bot.main generate-report "
            f"--config {config_path} --agents-config {agents_config_path} --run-id {target_run}"
        ),
        description="Generate markdown report for the selected run.",
    )
    eval_run = ReportShortcutResponse(
        label="Generate Eval (Run)",
        command=(
            "python -m prediction_market_bot.main generate-eval-report "
            f"--config {config_path} --agents-config {agents_config_path} --run-id {target_run}"
        ),
        description="Generate calibration/failure evaluation for the selected run.",
    )
    eval_window = ReportShortcutResponse(
        label="Evaluate Window",
        command=(
            "python -m prediction_market_bot.main evaluate-window "
            f"--config {config_path} --agents-config {agents_config_path} --date-from YYYY-MM-DD --date-to YYYY-MM-DD"
        ),
        description="Aggregate evaluation across a date window.",
    )
    return replay, generate_report, eval_run, eval_window


def _event_severity(*, event_type: str, payload: Mapping[str, Any]) -> str:
    text = event_type.lower()
    if any(token in text for token in ("critical", "failure", "failed", "error")):
        return "danger"
    if payload.get("ok") is False or _to_text(payload.get("status")).lower() in {"failed", "blocked", "rejected"}:
        return "danger"
    if any(token in text for token in ("blocked", "pause", "stale", "dropped", "degraded")):
        return "warning"
    return "info"


def _is_noteworthy_event(*, event_type: str, payload: Mapping[str, Any]) -> bool:
    text = event_type.lower()
    return any(
        token in text
        for token in (
            "pause",
            "resume",
            "blocked",
            "review",
            "settlement",
            "source",
            "stale",
            "failure",
            "error",
            "tx",
            "report",
            "eval",
            "critical",
        )
    ) or bool(_to_text(payload.get("error")))


def _event_summary(*, event_type: str, payload: Mapping[str, Any]) -> str:
    reason = _to_text(payload.get("reason")) or _to_text(payload.get("pause_reason"))
    error = _to_text(payload.get("error"))
    queue_id = _to_text(payload.get("queue_id"))
    request_id = _to_text(payload.get("request_id"))
    intent_id = _to_text(payload.get("intent_id"))
    action = _to_text(payload.get("action"))

    if event_type == "operator_pause":
        return f"Operator pause enabled. reason='{reason or 'not_set'}'."
    if event_type == "operator_resume":
        return "Operator pause cleared. Execution can resume."
    if event_type == "run_once_blocked_by_pause":
        return f"Run blocked by active operator pause. reason='{reason or 'not_set'}'."
    if "trade_review" in event_type:
        if queue_id:
            return f"Review event '{event_type}' on queue_id={queue_id}."
        return f"Review event '{event_type}' recorded."
    if "pending_settlement_request" in event_type:
        return f"Settlement request event '{event_type}' request_id={request_id or 'n/a'}."
    if event_type == "ui_operator_action":
        target = queue_id or intent_id or request_id or _to_text(payload.get("run_id")) or "n/a"
        return f"UI action '{action or 'unknown'}' status={_to_text(payload.get('status')) or 'n/a'} target={target}."
    if "report" in event_type or "eval" in event_type:
        return f"Report operation '{event_type}' completed."
    if error:
        return f"{event_type.replace('_', ' ')}: {error}"
    return event_type.replace("_", " ")


def _chart_from_counter(counter: Mapping[str, int], *, limit: int = 10) -> tuple[ChartPointResponse, ...]:
    items = sorted(counter.items(), key=lambda item: item[1], reverse=True)
    return tuple(ChartPointResponse(label=label, value=float(value)) for label, value in items[:limit] if label)


def _chart_from_mapping(raw: object, *, limit: int = 16) -> tuple[ChartPointResponse, ...]:
    if not isinstance(raw, Mapping):
        return ()
    points: list[ChartPointResponse] = []
    for key, value in raw.items():
        label = _to_text(key)
        if not label:
            continue
        points.append(ChartPointResponse(label=label, value=_to_float(value)))
    points.sort(key=lambda item: item.value, reverse=True)
    return tuple(points[:limit])


def _as_map(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_sequence(value: object) -> list[dict[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _deep_get(payload: Mapping[str, Any], *keys: str) -> object:
    current: object = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key, "")
    return current


def _extract_market_id(payload: Mapping[str, Any], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    direct = payload.get("market_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = _extract_market_id(dict(value), depth=depth + 1)
            if nested:
                return nested
    return ""


def _to_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _to_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ()


def _to_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return int(float(text))
            except ValueError:
                return 0
    return 0


def _to_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return float(text)
            except ValueError:
                return 0.0
    return 0.0


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _to_float(value)


def _normalize_review_filter(value: str | None) -> TradeReviewStatus | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized or normalized == "ALL":
        return None
    if normalized == TradeReviewStatus.PENDING.value:
        normalized = TradeReviewStatus.PENDING_REVIEW.value
    try:
        return TradeReviewStatus(normalized)
    except ValueError:
        return None


def _is_pending_review_status(status: TradeReviewStatus) -> bool:
    return status in {TradeReviewStatus.PENDING_REVIEW, TradeReviewStatus.PENDING}


def _review_status_counts(items: Sequence[TradeReviewItem]) -> dict[str, int]:
    counts = {
        TradeReviewStatus.PENDING_REVIEW.value: 0,
        TradeReviewStatus.APPROVED.value: 0,
        TradeReviewStatus.REJECTED.value: 0,
        TradeReviewStatus.EXPIRED.value: 0,
    }
    for item in items:
        status = item.status
        if _is_pending_review_status(status):
            counts[TradeReviewStatus.PENDING_REVIEW.value] += 1
            continue
        if status == TradeReviewStatus.APPROVED:
            counts[TradeReviewStatus.APPROVED.value] += 1
            continue
        if status == TradeReviewStatus.REJECTED:
            counts[TradeReviewStatus.REJECTED.value] += 1
            continue
        if status == TradeReviewStatus.EXPIRED:
            counts[TradeReviewStatus.EXPIRED.value] += 1
    return counts


def _review_row(item: TradeReviewItem) -> ReviewQueueRowResponse:
    effective_status = (
        TradeReviewStatus.PENDING_REVIEW.value if _is_pending_review_status(item.status) else item.status.value
    )
    return ReviewQueueRowResponse(
        queue_id=item.queue_id,
        run_id=item.run_id,
        market_id=item.market_id,
        side=item.side.value,
        status=effective_status,
        stake_usd=item.stake_usd,
        confidence=item.confidence,
        edge_bps=round(item.edge * 10_000.0, 2),
        expires_at=item.expires_at.isoformat() if item.expires_at else "",
        updated_at=item.updated_at.isoformat(),
    )


def _review_detail(item: TradeReviewItem) -> ReviewQueueDetailResponse:
    effective_status = (
        TradeReviewStatus.PENDING_REVIEW.value if _is_pending_review_status(item.status) else item.status.value
    )
    can_operate = _is_pending_review_status(item.status)
    return ReviewQueueDetailResponse(
        queue_id=item.queue_id,
        run_id=item.run_id,
        market_id=item.market_id,
        side=item.side.value,
        status=effective_status,
        stake_usd=item.stake_usd,
        confidence=item.confidence,
        edge_bps=round(item.edge * 10_000.0, 2),
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        expires_at=item.expires_at.isoformat() if item.expires_at else "",
        prediction_rationale=item.prediction_rationale,
        risk_rationale=item.risk_rationale,
        model_rationale=item.model_rationale,
        operator_rationale=item.operator_rationale,
        notes=item.notes,
        can_approve=can_operate,
        can_reject=can_operate,
    )


def _status_priority(status: str) -> int:
    weights = {
        TxConfirmationStatus.PENDING.value: 5,
        TxConfirmationStatus.DROPPED.value: 4,
        TxConfirmationStatus.REPLACED.value: 3,
        TxConfirmationStatus.FAILED.value: 2,
        TxConfirmationStatus.MINED.value: 1,
        TxConfirmationStatus.UNKNOWN.value: 0,
    }
    return weights.get(status, 0)


def _attempt_sort_key(item: TxAttempt) -> tuple[datetime, int]:
    submitted = item.submitted_at or datetime.min.replace(tzinfo=UTC)
    return submitted, item.retry_count


def _intent_id(intent: TxIntent) -> str:
    payload = (
        f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
        f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
    )
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
