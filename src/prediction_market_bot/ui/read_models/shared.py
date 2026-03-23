from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from urllib.parse import quote_plus

from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.domain.enums import TradeReviewStatus, TxConfirmationStatus
from prediction_market_bot.domain.models import TradeReviewItem, TxAttempt, TxIntent
from prediction_market_bot.services import RuntimeMetricsSnapshot
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    DriftAlertResponse,
    ExecutionTabResponse,
    IncidentBannerResponse,
    ModelVisibilityResponse,
    PredictionTabResponse,
    PositionsTabResponse,
    ProviderStatusSummaryResponse,
    ReportsTabResponse,
    ReportShortcutResponse,
    ResearchTabResponse,
    RiskTabResponse,
    RunSelectorResponse,
    RuntimeMetricsResponse,
    SandboxTxTabResponse,
    ScannerTabResponse,
    SettlementTabResponse,
    ShadowComparisonHistoryRowResponse,
)


def empty_scanner(selector: RunSelectorResponse, note: str) -> ScannerTabResponse:
    return ScannerTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        panel_status="warning",
        total_markets=0,
        eligible_markets_count=0,
        rejected_markets_count=0,
        candidates_count=0,
        avg_scan_score=0.0,
        funnel_summary=(),
        top_reasons=(),
        rejected_reasons=(),
        market_context_summary=(),
        diagnostics_summary=(),
        anomalies=(),
        candidates=(),
    )


def empty_research(selector: RunSelectorResponse, note: str) -> ResearchTabResponse:
    return ResearchTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        panel_status="warning",
        packets_count=0,
        findings_count=0,
        avg_evidence_strength=0.0,
        avg_disagreement_score=0.0,
        source_failures_count=0,
        coverage_summary=(),
        source_type_distribution=(),
        diagnostics_summary=(),
        anomalies=(),
        packets=(),
    )


def empty_prediction(
    selector: RunSelectorResponse,
    note: str,
    *,
    model_visibility: ModelVisibilityResponse,
    shadow_history: tuple[ShadowComparisonHistoryRowResponse, ...] = (),
    drift_alert: DriftAlertResponse | None = None,
) -> PredictionTabResponse:
    return PredictionTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        panel_status="warning",
        predictions_count=0,
        avg_confidence=0.0,
        avg_edge_bps=0.0,
        avg_probability_gap=0.0,
        parity_warning_count=0,
        model_visibility=model_visibility,
        calibration_summary=(),
        shadow_comparison=None,
        shadow_history=shadow_history,
        approval_rate_summary=(),
        disagreement_buckets=(),
        enrichment_coverage=None,
        disagreement_vs_baseline=(),
        drift_alert=drift_alert,
        diagnostics_summary=(),
        anomalies=(),
        side_distribution=(),
        rows=(),
    )


def empty_risk(selector: RunSelectorResponse, note: str) -> RiskTabResponse:
    return RiskTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        panel_status="warning",
        decisions_count=0,
        approved_count=0,
        blocked_count=0,
        avg_stake_usd=0.0,
        proposed_stake_usd=0.0,
        approved_stake_usd=0.0,
        avg_portfolio_exposure_usd=0.0,
        avg_market_exposure_usd=0.0,
        daily_stop_triggered=False,
        circuit_breaker_active=False,
        guardrail_distribution=(),
        reason_code_distribution=(),
        diagnostics_summary=(),
        anomalies=(),
        rows=(),
    )


def empty_execution(selector: RunSelectorResponse, note: str) -> ExecutionTabResponse:
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


def empty_settlement(selector: RunSelectorResponse, note: str) -> SettlementTabResponse:
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


def empty_sandbox_tx(selector: RunSelectorResponse, note: str) -> SandboxTxTabResponse:
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


def empty_positions(selector: RunSelectorResponse, note: str) -> PositionsTabResponse:
    return PositionsTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=False,
        note=note,
        active_market_id="",
        open_positions_count=0,
        linked_review_count=0,
        linked_tx_count=0,
        total_exposure_usd=0.0,
        unrealized_pnl_usd=0.0,
        realized_pnl_usd=0.0,
        total_pnl_usd=0.0,
        exposure_distribution=(),
        rows=(),
    )


def empty_reports(
    selector: RunSelectorResponse,
    note: str,
    *,
    config_path: str,
    agents_config_path: str,
) -> ReportsTabResponse:
    replay_shortcut, generate_report_shortcut, eval_run_shortcut, eval_window_shortcut = report_shortcuts(
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
        run_overview_url="",
        review_queue_url="",
        sandbox_tx_url="",
        positions_url="",
        settlement_url="",
        replay_shortcut=replay_shortcut,
        generate_report_shortcut=generate_report_shortcut,
        eval_run_shortcut=eval_run_shortcut,
        eval_window_shortcut=eval_window_shortcut,
        incidents_feed=(),
    )


def to_metrics_response(snapshot: RuntimeMetricsSnapshot) -> RuntimeMetricsResponse:
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


def provider_status(settings: AppSettings, metrics: RuntimeMetricsResponse) -> ProviderStatusSummaryResponse:
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


def incident_banners(
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


def report_shortcuts(
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


def incident_component(*, event_type: str, payload: Mapping[str, Any]) -> str:
    text = event_type.lower()
    if "review" in text or _search_text(payload, "queue_id", "review_queue_id"):
        return "review_queue"
    if "tx" in text or "transaction" in text or _search_text(payload, "intent_id"):
        return "sandbox_tx"
    if "settlement" in text or _search_text(payload, "request_id"):
        return "settlement"
    if "report" in text or "replay" in text or "eval" in text:
        return "reports_replay"
    if (
        "source" in text
        or "market_fetch" in text
        or "research_ingestion" in text
        or _search_text(payload, "source", "source_name")
    ):
        return "providers"
    if "startup" in text or "healthcheck" in text or "db_" in text:
        return "system"
    if "pause" in text or "resume" in text or "run_once" in text or text == "ui_operator_action":
        return "operator_control"
    return "runtime"


def incident_context(*, event_type: str, payload: Mapping[str, Any], event_run_id: str) -> dict[str, str]:
    queue_id = _search_text(payload, "queue_id", "review_queue_id")
    intent_id = _search_text(payload, "intent_id")
    request_id = _search_text(payload, "request_id")
    market_id = _search_text(payload, "market_id") or extract_market_id(payload)
    source = _search_text(payload, "source", "source_name")
    payload_run_id = _search_text(payload, "run_id")
    linked_run_id = _resolved_linked_run_id(event_run_id=event_run_id, payload_run_id=payload_run_id)
    reason_code = (
        _search_text(payload, "reason_code", "classification", "reason", "pause_reason")
        or _search_text(payload, "status")
        or _search_text(payload, "error")
    )
    affected_target = queue_id or intent_id or request_id or market_id or source or linked_run_id or "n/a"
    return {
        "linked_run_id": linked_run_id,
        "component": incident_component(event_type=event_type, payload=payload),
        "affected_target": affected_target,
        "reason_code": reason_code,
        "queue_id": queue_id,
        "intent_id": intent_id,
        "request_id": request_id,
        "market_id": market_id,
        "source": source,
    }


def incident_links(*, linked_run_id: str, queue_id: str, intent_id: str, market_id: str) -> dict[str, str]:
    run = linked_run_id.strip()
    if not run:
        return {
            "run_url": "",
            "review_queue_url": "",
            "sandbox_tx_url": "",
            "position_url": "",
            "settlement_url": "",
            "reports_url": "",
        }
    run_token = quote_plus(run)
    root = f"/?run_id={run_token}"
    review_url = f"{root}&active_tab=review-queue&review_status=ALL"
    if queue_id.strip():
        review_url = f"{review_url}&review_queue_id={quote_plus(queue_id)}"
    sandbox_url = f"{root}&active_tab=sandbox-tx"
    if intent_id.strip():
        sandbox_url = f"{sandbox_url}&tx_intent_id={quote_plus(intent_id)}"
    position_url = f"{root}&active_tab=positions"
    if market_id.strip():
        position_url = f"{position_url}&position_market_id={quote_plus(market_id)}"
    return {
        "run_url": f"{root}&active_tab=overview",
        "review_queue_url": review_url,
        "sandbox_tx_url": sandbox_url,
        "position_url": position_url,
        "settlement_url": f"{root}&active_tab=settlement",
        "reports_url": f"{root}&active_tab=reports",
    }


def event_severity(*, event_type: str, payload: Mapping[str, Any]) -> str:
    text = event_type.lower()
    if any(token in text for token in ("critical", "failure", "failed", "error")):
        return "danger"
    if payload.get("ok") is False or to_text(payload.get("status")).lower() in {"failed", "blocked", "rejected"}:
        return "danger"
    if any(token in text for token in ("blocked", "pause", "stale", "dropped", "degraded")):
        return "warning"
    return "info"


def is_noteworthy_event(*, event_type: str, payload: Mapping[str, Any]) -> bool:
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
    ) or bool(to_text(payload.get("error")))


def event_summary(*, event_type: str, payload: Mapping[str, Any]) -> str:
    reason = to_text(payload.get("reason")) or to_text(payload.get("pause_reason"))
    error = to_text(payload.get("error"))
    queue_id = to_text(payload.get("queue_id"))
    request_id = to_text(payload.get("request_id"))
    intent_id = to_text(payload.get("intent_id"))
    action = to_text(payload.get("action"))

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
        target = queue_id or intent_id or request_id or to_text(payload.get("run_id")) or "n/a"
        return f"UI action '{action or 'unknown'}' status={to_text(payload.get('status')) or 'n/a'} target={target}."
    if "report" in event_type or "eval" in event_type:
        return f"Report operation '{event_type}' completed."
    if error:
        return f"{event_type.replace('_', ' ')}: {error}"
    return event_type.replace("_", " ")


def _resolved_linked_run_id(*, event_run_id: str, payload_run_id: str) -> str:
    for candidate in (payload_run_id.strip(), event_run_id.strip()):
        if candidate and candidate not in {"operator", "ui-control-plane"}:
            return candidate
    return ""


def _search_text(payload: Mapping[str, Any], *keys: str, depth: int = 0) -> str:
    if depth > 5:
        return ""
    for key in keys:
        if key in payload:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = _search_text(dict(value), *keys, depth=depth + 1)
            if nested:
                return nested
    return ""


def chart_from_counter(counter: Mapping[str, int], *, limit: int = 10) -> tuple[ChartPointResponse, ...]:
    items = sorted(counter.items(), key=lambda item: item[1], reverse=True)
    return tuple(ChartPointResponse(label=label, value=float(value)) for label, value in items[:limit] if label)


def chart_from_mapping(
    raw: object,
    *,
    limit: int = 16,
    preferred_order: Sequence[str] = (),
) -> tuple[ChartPointResponse, ...]:
    if not isinstance(raw, Mapping):
        return ()
    preferred_map = {item.strip(): index for index, item in enumerate(preferred_order) if item.strip()}
    points: list[ChartPointResponse] = []
    order_key: dict[str, int] = {}
    for key, value in raw.items():
        label = to_text(key)
        if not label:
            continue
        if label in preferred_map:
            order_key[label] = preferred_map[label]
        points.append(ChartPointResponse(label=label, value=to_float(value)))
    if preferred_map:
        points.sort(
            key=lambda item: (
                order_key.get(item.label, len(preferred_map) + 1),
                -item.value,
            )
        )
    else:
        points.sort(key=lambda item: item.value, reverse=True)
    return tuple(points[:limit])


def as_map(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def as_sequence(value: object) -> list[dict[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def deep_get(payload: Mapping[str, Any], *keys: str) -> object:
    current: object = payload
    for key in keys:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key, "")
    return current


def extract_market_id(payload: Mapping[str, Any], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    direct = payload.get("market_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = extract_market_id(dict(value), depth=depth + 1)
            if nested:
                return nested
    return ""


def to_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def to_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return ()


def to_int(value: object) -> int:
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


def to_float(value: object) -> float:
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


def to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    return to_float(value)


def normalize_review_filter(value: str | None) -> TradeReviewStatus | None:
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


def is_pending_review_status(status: TradeReviewStatus) -> bool:
    return status in {TradeReviewStatus.PENDING_REVIEW, TradeReviewStatus.PENDING}


def review_status_counts(items: Sequence[TradeReviewItem]) -> dict[str, int]:
    counts = {
        TradeReviewStatus.PENDING_REVIEW.value: 0,
        TradeReviewStatus.APPROVED.value: 0,
        TradeReviewStatus.REJECTED.value: 0,
        TradeReviewStatus.EXPIRED.value: 0,
    }
    for item in items:
        status = item.status
        if is_pending_review_status(status):
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


def status_priority(status: str) -> int:
    weights = {
        TxConfirmationStatus.PENDING.value: 5,
        TxConfirmationStatus.DROPPED.value: 4,
        TxConfirmationStatus.REPLACED.value: 3,
        TxConfirmationStatus.FAILED.value: 2,
        TxConfirmationStatus.MINED.value: 1,
        TxConfirmationStatus.UNKNOWN.value: 0,
    }
    return weights.get(status, 0)


def attempt_sort_key(item: TxAttempt) -> tuple[datetime, int]:
    submitted = item.submitted_at or datetime.min.replace(tzinfo=UTC)
    return submitted, item.retry_count


def intent_id(intent: TxIntent) -> str:
    payload = (
        f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
        f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
    )
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
