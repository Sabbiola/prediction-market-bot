from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Mapping, Sequence

from prediction_market_bot.infrastructure.persistence import JsonlPersistence

from .history_queries import parse_timestamp
from .run_summary_service import (
    REPLAY_ARTIFACT_TYPES,
    BrierMetrics,
    CalibrationMetrics,
    ReplayDecisionRecord,
    ReplaySummary,
)


def replay_run(persistence: JsonlPersistence, run_id: str) -> ReplaySummary:
    timings_ms: dict[str, float] = {}

    read_events_started = perf_counter()
    events = persistence.read_run_events(run_id)
    timings_ms["read_events"] = round(max((perf_counter() - read_events_started) * 1000.0, 0.0), 3)

    read_artifacts_started = perf_counter()
    artifact_rows = {name: persistence.read_artifact_records(run_id, name) for name in REPLAY_ARTIFACT_TYPES}
    timings_ms["read_artifacts"] = round(max((perf_counter() - read_artifacts_started) * 1000.0, 0.0), 3)

    summary_payload = _latest_summary_payload(artifact_rows["pipeline_summaries"])

    execution_rows = artifact_rows["execution_results"]
    settlement_rows = artifact_rows["settlement_results"]

    reconstruct_started = perf_counter()
    reconstructed_records = _reconstruct_records(artifact_rows)
    timings_ms["reconstruct_records"] = round(max((perf_counter() - reconstruct_started) * 1000.0, 0.0), 3)

    metrics_started = perf_counter()
    calibration_metrics, brier_metrics = _compute_prediction_metrics(reconstructed_records)
    failure_categories = _classify_failures(reconstructed_records)
    timings_ms["compute_metrics"] = round(max((perf_counter() - metrics_started) * 1000.0, 0.0), 3)

    total_markets = len(artifact_rows["market_snapshots"])
    candidates = len(artifact_rows["prediction_results"])
    executed = sum(1 for row in execution_rows if _payload_value(row, "status") == "FILLED")
    settled = len(settlement_rows)
    wins = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "WIN")
    losses = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "LOSS")
    skipped = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "SKIPPED")
    observability_started = perf_counter()
    observability = _build_observability_payload(
        run_id=run_id,
        summary_payload=summary_payload,
        reconstructed_records=reconstructed_records,
        executed=executed,
        candidates=candidates,
        settlement_request_rows=artifact_rows["pending_settlement_requests"],
        portfolio_snapshot_rows=artifact_rows["paper_open_positions_state"]
        or artifact_rows["paper_portfolio_snapshots"],
        analysis_timings_ms=timings_ms,
    )
    timings_ms["build_observability"] = round(max((perf_counter() - observability_started) * 1000.0, 0.0), 3)

    time_bounds_started = perf_counter()
    started_at, finished_at = _event_time_bounds(events)
    if started_at is None or finished_at is None:
        started_at, finished_at = _artifact_time_bounds(artifact_rows)
    if started_at is None:
        started_at = parse_timestamp(summary_payload.get("started_at")) if summary_payload else None
    if finished_at is None:
        finished_at = parse_timestamp(summary_payload.get("finished_at")) if summary_payload else None
    timings_ms["resolve_time_bounds"] = round(max((perf_counter() - time_bounds_started) * 1000.0, 0.0), 3)
    timings_ms["total_query"] = round(sum(timings_ms.values()), 3)
    observability["analysis_timings_ms"] = dict(sorted(timings_ms.items()))

    return ReplaySummary(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        total_markets=total_markets,
        candidates=candidates,
        executed=executed,
        settled=settled,
        wins=wins,
        losses=losses,
        skipped=skipped,
        artifact_counts={name: len(rows) for name, rows in artifact_rows.items()},
        event_count=len(events),
        reconstructed_records=reconstructed_records,
        calibration_metrics=calibration_metrics,
        brier_metrics=brier_metrics,
        failure_categories=failure_categories,
        observability=observability,
    )


def _latest_summary_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    latest_payload: Mapping[str, Any] = {}
    latest_ts: datetime | None = None
    for row in rows:
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, Mapping):
            continue
        payload = dict(payload_raw)
        timestamp = parse_timestamp(row.get("timestamp"))
        if latest_ts is None or (timestamp is not None and timestamp > latest_ts):
            latest_ts = timestamp
            latest_payload = payload
    return latest_payload


def _build_observability_payload(
    *,
    run_id: str,
    summary_payload: Mapping[str, Any],
    reconstructed_records: Sequence[ReplayDecisionRecord],
    executed: int,
    candidates: int,
    settlement_request_rows: Sequence[Mapping[str, Any]],
    portfolio_snapshot_rows: Sequence[Mapping[str, Any]],
    analysis_timings_ms: Mapping[str, float],
) -> dict[str, Any]:
    counters = _coerce_counter_map(summary_payload.get("counters"))
    if not counters:
        counters = {
            "candidate_markets": candidates,
            "rejected_trades": sum(
                1
                for record in reconstructed_records
                if record.risk is not None and not bool(record.risk.get("approved", False))
            ),
            "executed_paper_trades": executed,
            "stale_data_events": sum(
                1
                for record in reconstructed_records
                for reason in _coerce_text_list(record.risk.get("reasoning") if record.risk else ())
                if "stale_market_data" in reason
            ),
            "source_failures": 0,
        }

    settlement_queue = _settlement_request_state_counts(settlement_request_rows)
    portfolio_snapshot = _latest_payload(portfolio_snapshot_rows)

    return {
        "correlation_id": _to_text(summary_payload.get("correlation_id")) or run_id,
        "status": _to_text(summary_payload.get("status")) or "unknown",
        "counters": counters,
        "stage_timings_ms": _coerce_timing_map(summary_payload.get("stage_timings_ms")),
        "analysis_timings_ms": dict(analysis_timings_ms),
        "settlement_queue": settlement_queue,
        "portfolio": {
            "open_positions": _to_int(portfolio_snapshot.get("open_position_count", portfolio_snapshot.get("position_count", 0))),
            "settled_positions": _to_int(portfolio_snapshot.get("settled_position_count")),
            "total_exposure_usd": round(_to_float(portfolio_snapshot.get("total_exposure_usd")), 2),
            "realized_pnl_usd": round(_to_float(portfolio_snapshot.get("realized_pnl_usd")), 2),
            "unrealized_pnl_usd": round(_to_float(portfolio_snapshot.get("unrealized_pnl_usd")), 2),
        },
    }


def _coerce_counter_map(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        normalized = _to_int(raw)
        if normalized >= 0:
            result[key] = normalized
    return result


def _coerce_timing_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        normalized = _to_float(raw)
        if normalized >= 0.0:
            result[key] = round(normalized, 3)
    return result


def _latest_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    latest_payload: Mapping[str, Any] = {}
    latest_ts: datetime | None = None
    for row in rows:
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, Mapping):
            continue
        payload = dict(payload_raw)
        timestamp = parse_timestamp(row.get("timestamp"))
        if latest_ts is None or (timestamp is not None and timestamp > latest_ts):
            latest_ts = timestamp
            latest_payload = payload
    return latest_payload


def _settlement_request_state_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    latest_by_request: dict[str, tuple[datetime | None, Mapping[str, Any]]] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        request_id = _to_text(payload.get("request_id"))
        if not request_id:
            continue
        timestamp = parse_timestamp(row.get("timestamp"))
        current = latest_by_request.get(request_id)
        if current is None:
            latest_by_request[request_id] = (timestamp, payload)
            continue
        current_ts = current[0]
        if timestamp is None or (current_ts is not None and timestamp <= current_ts):
            continue
        latest_by_request[request_id] = (timestamp, payload)

    counts = {"pending": 0, "settled": 0, "total": 0}
    for _, payload in latest_by_request.values():
        state = _to_text(payload.get("state")).upper()
        counts["total"] += 1
        if state == "SETTLED":
            counts["settled"] += 1
        else:
            counts["pending"] += 1
    return counts


def _reconstruct_records(artifact_rows: Mapping[str, list[dict[str, Any]]]) -> tuple[ReplayDecisionRecord, ...]:
    stage_to_artifact = {
        "scan": "market_candidates",
        "research": "research_packets",
        "prediction": "prediction_results",
        "risk": "effective_risk_decisions",
        "execution": "execution_results",
        "settlement": "settlement_results",
        "postmortem": "postmortems",
    }
    stage_maps: dict[str, dict[str, Mapping[str, Any]]] = {}
    market_ids: set[str] = set()

    for stage, artifact_type in stage_to_artifact.items():
        rows = artifact_rows.get(artifact_type, [])
        if stage == "risk" and not rows:
            rows = artifact_rows.get("risk_decisions", [])
        stage_map = _build_stage_index(rows)
        stage_maps[stage] = stage_map
        market_ids.update(stage_map.keys())

    records = [
        ReplayDecisionRecord(
            market_id=market_id,
            scan=stage_maps["scan"].get(market_id),
            research=stage_maps["research"].get(market_id),
            prediction=stage_maps["prediction"].get(market_id),
            risk=stage_maps["risk"].get(market_id),
            execution=stage_maps["execution"].get(market_id),
            settlement=stage_maps["settlement"].get(market_id),
            postmortem=stage_maps["postmortem"].get(market_id),
        )
        for market_id in sorted(market_ids)
    ]
    return tuple(records)


def _build_stage_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, tuple[datetime | None, Mapping[str, Any]]] = {}
    for row in rows:
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, Mapping):
            continue
        payload = dict(payload_raw)
        market_id = _extract_market_id(payload)
        if market_id is None:
            continue
        timestamp = parse_timestamp(row.get("timestamp"))
        current = indexed.get(market_id)
        if current is None:
            indexed[market_id] = (timestamp, payload)
            continue
        current_ts = current[0]
        if timestamp is None or (current_ts is not None and timestamp <= current_ts):
            continue
        indexed[market_id] = (timestamp, payload)
    return {market_id: payload for market_id, (_, payload) in indexed.items()}


def _compute_prediction_metrics(records: Sequence[ReplayDecisionRecord]) -> tuple[CalibrationMetrics, BrierMetrics]:
    fair_yes_probs: list[float] = []
    outcomes_yes: list[int] = []
    confidences: list[float] = []
    correctness: list[int] = []

    for record in records:
        if record.prediction is None or record.settlement is None:
            continue
        fair_yes_prob = _to_float(record.prediction.get("fair_yes_prob"))
        resolved_yes = bool(record.settlement.get("resolved_yes"))
        fair_yes_probs.append(_clamp(fair_yes_prob, lower=0.0, upper=1.0))
        outcomes_yes.append(1 if resolved_yes else 0)

        confidence = _to_float(record.prediction.get("confidence"))
        confidences.append(_clamp(confidence, lower=0.0, upper=1.0))

        selected_side = _to_text(record.prediction.get("selected_side"))
        is_correct = (selected_side == "YES" and resolved_yes) or (selected_side == "NO" and not resolved_yes)
        correctness.append(1 if is_correct else 0)

    sample_size = len(fair_yes_probs)
    if sample_size == 0:
        return CalibrationMetrics.empty(), BrierMetrics.empty()

    mean_fair_yes_prob = sum(fair_yes_probs) / sample_size
    observed_yes_rate = sum(outcomes_yes) / sample_size
    calibration_gap = abs(mean_fair_yes_prob - observed_yes_rate)
    expected_calibration_error = _expected_calibration_error(fair_yes_probs, outcomes_yes, bins=10)
    mean_confidence = sum(confidences) / sample_size
    selected_side_accuracy = sum(correctness) / sample_size
    confidence_accuracy_gap = abs(mean_confidence - selected_side_accuracy)

    fair_yes_brier = sum((prob - outcome) ** 2 for prob, outcome in zip(fair_yes_probs, outcomes_yes, strict=False)) / sample_size
    confidence_brier = sum((confidence - correct) ** 2 for confidence, correct in zip(confidences, correctness, strict=False)) / sample_size

    calibration = CalibrationMetrics(
        sample_size=sample_size,
        mean_fair_yes_prob=mean_fair_yes_prob,
        observed_yes_rate=observed_yes_rate,
        calibration_gap=calibration_gap,
        expected_calibration_error=expected_calibration_error,
        mean_confidence=mean_confidence,
        selected_side_accuracy=selected_side_accuracy,
        confidence_accuracy_gap=confidence_accuracy_gap,
    )
    brier = BrierMetrics(
        sample_size=sample_size,
        fair_yes_brier_score=fair_yes_brier,
        confidence_brier_score=confidence_brier,
    )
    return calibration, brier


def _classify_failures(records: Sequence[ReplayDecisionRecord]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for record in records:
        if not _is_complete_record(record):
            _inc(categories, "pipeline_integrity")

        if record.risk:
            approved = bool(record.risk.get("approved", False))
            if not approved:
                reasons = _coerce_text_list(record.risk.get("reasoning"))
                if not reasons:
                    _inc(categories, "pipeline_integrity")
                for reason in reasons:
                    category = _reason_category(reason)
                    if category is not None:
                        _inc(categories, category)

        execution_status = _to_text(record.execution.get("status")) if record.execution else ""
        if execution_status == "FAILED":
            _inc(categories, "execution_failure")

        settlement_outcome = _to_text(record.settlement.get("outcome_classification")) if record.settlement else ""
        if settlement_outcome == "SKIPPED" and execution_status == "FILLED":
            _inc(categories, "settlement_failure")

        if record.postmortem:
            for cause in _coerce_text_list(record.postmortem.get("causes")):
                category = _postmortem_category(cause)
                if category is not None:
                    _inc(categories, category)
    return categories


def _is_complete_record(record: ReplayDecisionRecord) -> bool:
    return all(
        (
            record.scan is not None,
            record.research is not None,
            record.prediction is not None,
            record.risk is not None,
            record.execution is not None,
        )
    )


def _reason_category(reason: str) -> str | None:
    text = reason.lower()
    if "approved" in text:
        return None
    if "manual_pause" in text or "circuit_breaker" in text or "daily_stop" in text:
        return "safety_controls"
    if "liquidity_" in text or "spread_" in text or "stale_market_data" in text:
        return "data_quality"
    if "confidence_below_threshold" in text or "edge_below_threshold" in text:
        return "signal_quality"
    if "exposure" in text or "stake_below_min_bet" in text:
        return "risk_limits"
    if "rejected_without_reason" in text:
        return "pipeline_integrity"
    return "risk_other"


def _postmortem_category(cause: str) -> str | None:
    if cause == "DATA_GAP":
        return "data_quality"
    if cause in {"RESEARCH_NOISE", "CALIBRATION_ERROR"}:
        return "calibration_failure"
    if cause == "EXECUTION_SLIPPAGE":
        return "execution_failure"
    if cause == "RESOLUTION_MISREAD":
        return "settlement_failure"
    if cause == "RISK_OVERSIZING":
        return "risk_limits"
    if cause == "LIQUIDITY_TRAP":
        return "data_quality"
    return None


def _expected_calibration_error(probabilities: Sequence[float], outcomes: Sequence[int], *, bins: int) -> float:
    if not probabilities:
        return 0.0
    total = len(probabilities)
    ece = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        bucket_probs: list[float] = []
        bucket_outcomes: list[int] = []
        for prob, outcome in zip(probabilities, outcomes, strict=False):
            in_bucket = lower <= prob < upper or (index == bins - 1 and prob <= upper)
            if in_bucket:
                bucket_probs.append(prob)
                bucket_outcomes.append(outcome)
        if not bucket_probs:
            continue
        mean_prob = sum(bucket_probs) / len(bucket_probs)
        mean_outcome = sum(bucket_outcomes) / len(bucket_outcomes)
        ece += abs(mean_prob - mean_outcome) * (len(bucket_probs) / total)
    return ece


def _extract_market_id(payload: Mapping[str, Any], *, depth: int = 0) -> str | None:
    if depth > 5:
        return None
    direct = payload.get("market_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = _extract_market_id(value, depth=depth + 1)
            if nested is not None:
                return nested
    return None


def _payload_value(row: Mapping[str, Any], key: str) -> Any:
    payload = row.get("payload")
    if isinstance(payload, Mapping):
        return payload.get(key)
    return None


def _event_time_bounds(events: Sequence[Mapping[str, Any]]) -> tuple[datetime | None, datetime | None]:
    timestamps = [parse_timestamp(row.get("timestamp")) for row in events]
    valid = [item for item in timestamps if item is not None]
    if not valid:
        return None, None
    return min(valid), max(valid)


def _artifact_time_bounds(artifact_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[datetime | None, datetime | None]:
    timestamps: list[datetime] = []
    for rows in artifact_rows.values():
        for row in rows:
            parsed = parse_timestamp(row.get("timestamp"))
            if parsed is not None:
                timestamps.append(parsed)
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result: list[str] = []
        for item in value:
            text = _to_text(item)
            if text:
                result.append(text)
        return result
    text = _to_text(value)
    return [text] if text else []


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def _clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _inc(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1
