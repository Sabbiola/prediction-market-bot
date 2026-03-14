from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence

from prediction_market_bot.infrastructure.persistence import JsonlPersistence

REPLAY_ARTIFACT_TYPES: tuple[str, ...] = (
    "market_snapshots",
    "market_candidates",
    "research_packets",
    "prediction_results",
    "risk_decisions",
    "effective_risk_decisions",
    "trade_review_candidates",
    "trade_review_decisions",
    "trade_review_gate_decisions",
    "order_intents",
    "execution_results",
    "transaction_attempts",
    "pending_settlement_requests",
    "resolution_checks",
    "settlement_results",
    "postmortems",
    "paper_portfolio_events",
    "paper_portfolio_snapshots",
    "paper_open_positions_state",
    "http_error_metadata",
    "pipeline_summaries",
)


@dataclass(slots=True, frozen=True)
class ReplayDecisionRecord:
    market_id: str
    scan: Mapping[str, Any] | None
    research: Mapping[str, Any] | None
    prediction: Mapping[str, Any] | None
    risk: Mapping[str, Any] | None
    execution: Mapping[str, Any] | None
    settlement: Mapping[str, Any] | None
    postmortem: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "scan": dict(self.scan) if self.scan else None,
            "research": dict(self.research) if self.research else None,
            "prediction": dict(self.prediction) if self.prediction else None,
            "risk": dict(self.risk) if self.risk else None,
            "execution": dict(self.execution) if self.execution else None,
            "settlement": dict(self.settlement) if self.settlement else None,
            "postmortem": dict(self.postmortem) if self.postmortem else None,
        }


@dataclass(slots=True, frozen=True)
class CalibrationMetrics:
    sample_size: int
    mean_fair_yes_prob: float
    observed_yes_rate: float
    calibration_gap: float
    expected_calibration_error: float
    mean_confidence: float
    selected_side_accuracy: float
    confidence_accuracy_gap: float

    @classmethod
    def empty(cls) -> "CalibrationMetrics":
        return cls(
            sample_size=0,
            mean_fair_yes_prob=0.0,
            observed_yes_rate=0.0,
            calibration_gap=0.0,
            expected_calibration_error=0.0,
            mean_confidence=0.0,
            selected_side_accuracy=0.0,
            confidence_accuracy_gap=0.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "mean_fair_yes_prob": round(self.mean_fair_yes_prob, 6),
            "observed_yes_rate": round(self.observed_yes_rate, 6),
            "calibration_gap": round(self.calibration_gap, 6),
            "expected_calibration_error": round(self.expected_calibration_error, 6),
            "mean_confidence": round(self.mean_confidence, 6),
            "selected_side_accuracy": round(self.selected_side_accuracy, 6),
            "confidence_accuracy_gap": round(self.confidence_accuracy_gap, 6),
        }


@dataclass(slots=True, frozen=True)
class BrierMetrics:
    sample_size: int
    fair_yes_brier_score: float | None
    confidence_brier_score: float | None

    @classmethod
    def empty(cls) -> "BrierMetrics":
        return cls(sample_size=0, fair_yes_brier_score=None, confidence_brier_score=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "fair_yes_brier_score": round(self.fair_yes_brier_score, 6) if self.fair_yes_brier_score is not None else None,
            "confidence_brier_score": round(self.confidence_brier_score, 6)
            if self.confidence_brier_score is not None
            else None,
        }


@dataclass(slots=True, frozen=True)
class ReplaySummary:
    run_id: str
    started_at: datetime | None
    finished_at: datetime | None
    total_markets: int
    candidates: int
    executed: int
    settled: int
    wins: int
    losses: int
    skipped: int
    artifact_counts: dict[str, int]
    event_count: int
    reconstructed_records: tuple[ReplayDecisionRecord, ...]
    calibration_metrics: CalibrationMetrics
    brier_metrics: BrierMetrics
    failure_categories: dict[str, int]
    observability: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_records: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_markets": self.total_markets,
            "candidates": self.candidates,
            "executed": self.executed,
            "settled": self.settled,
            "wins": self.wins,
            "losses": self.losses,
            "skipped": self.skipped,
            "artifact_counts": dict(sorted(self.artifact_counts.items())),
            "event_count": self.event_count,
            "reconstructed_count": len(self.reconstructed_records),
            "calibration_metrics": self.calibration_metrics.to_dict(),
            "brier_metrics": self.brier_metrics.to_dict(),
            "failure_categories": dict(sorted(self.failure_categories.items())),
            "observability": dict(self.observability),
        }
        if include_records:
            payload["records"] = [record.to_dict() for record in self.reconstructed_records]
        return payload


@dataclass(slots=True, frozen=True)
class WindowEvaluation:
    date_from: datetime | None
    date_to: datetime | None
    run_ids: tuple[str, ...]
    run_count: int
    total_markets: int
    candidates: int
    executed: int
    settled: int
    wins: int
    losses: int
    skipped: int
    event_count: int
    artifact_counts: dict[str, int]
    calibration_metrics: CalibrationMetrics
    brier_metrics: BrierMetrics
    failure_categories: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "run_ids": list(self.run_ids),
            "run_count": self.run_count,
            "total_markets": self.total_markets,
            "candidates": self.candidates,
            "executed": self.executed,
            "settled": self.settled,
            "wins": self.wins,
            "losses": self.losses,
            "skipped": self.skipped,
            "event_count": self.event_count,
            "artifact_counts": dict(sorted(self.artifact_counts.items())),
            "calibration_metrics": self.calibration_metrics.to_dict(),
            "brier_metrics": self.brier_metrics.to_dict(),
            "failure_categories": dict(sorted(self.failure_categories.items())),
        }


def replay_run(persistence: JsonlPersistence, run_id: str) -> ReplaySummary:
    events = persistence.read_run_events(run_id)
    artifact_rows = {name: persistence.read_artifact_records(run_id, name) for name in REPLAY_ARTIFACT_TYPES}
    summary_payload = _latest_summary_payload(artifact_rows["pipeline_summaries"])

    execution_rows = artifact_rows["execution_results"]
    settlement_rows = artifact_rows["settlement_results"]
    reconstructed_records = _reconstruct_records(artifact_rows)
    calibration_metrics, brier_metrics = _compute_prediction_metrics(reconstructed_records)
    failure_categories = _classify_failures(reconstructed_records)

    total_markets = len(artifact_rows["market_snapshots"])
    candidates = len(artifact_rows["prediction_results"])
    executed = sum(1 for row in execution_rows if _payload_value(row, "status") == "FILLED")
    settled = len(settlement_rows)
    wins = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "WIN")
    losses = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "LOSS")
    skipped = sum(1 for row in settlement_rows if _payload_value(row, "outcome_classification") == "SKIPPED")
    observability = _build_observability_payload(
        run_id=run_id,
        summary_payload=summary_payload,
        reconstructed_records=reconstructed_records,
        executed=executed,
        candidates=candidates,
        settlement_request_rows=artifact_rows["pending_settlement_requests"],
        portfolio_snapshot_rows=artifact_rows["paper_open_positions_state"]
        or artifact_rows["paper_portfolio_snapshots"],
    )

    started_at, finished_at = _event_time_bounds(events)
    if started_at is None or finished_at is None:
        started_at, finished_at = _artifact_time_bounds(artifact_rows)
    if started_at is None:
        started_at = _parse_timestamp(summary_payload.get("started_at")) if summary_payload else None
    if finished_at is None:
        finished_at = _parse_timestamp(summary_payload.get("finished_at")) if summary_payload else None

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


def evaluate_window(
    persistence: JsonlPersistence,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit_runs: int = 50,
) -> WindowEvaluation:
    selected_run_ids = list_run_ids(
        persistence,
        date_from=date_from,
        date_to=date_to,
        limit_runs=limit_runs,
    )
    summaries = [replay_run(persistence, run_id) for run_id in selected_run_ids]

    if not summaries:
        return WindowEvaluation(
            date_from=_date_to_start(date_from),
            date_to=_date_to_end(date_to),
            run_ids=(),
            run_count=0,
            total_markets=0,
            candidates=0,
            executed=0,
            settled=0,
            wins=0,
            losses=0,
            skipped=0,
            event_count=0,
            artifact_counts={},
            calibration_metrics=CalibrationMetrics.empty(),
            brier_metrics=BrierMetrics.empty(),
            failure_categories={},
        )

    artifact_counts: dict[str, int] = {}
    failure_categories: dict[str, int] = {}
    for summary in summaries:
        for artifact_type, count in summary.artifact_counts.items():
            artifact_counts[artifact_type] = artifact_counts.get(artifact_type, 0) + count
        for category, count in summary.failure_categories.items():
            failure_categories[category] = failure_categories.get(category, 0) + count

    calibration = _aggregate_calibration([summary.calibration_metrics for summary in summaries])
    brier = _aggregate_brier([summary.brier_metrics for summary in summaries])

    return WindowEvaluation(
        date_from=_date_to_start(date_from),
        date_to=_date_to_end(date_to),
        run_ids=tuple(selected_run_ids),
        run_count=len(selected_run_ids),
        total_markets=sum(summary.total_markets for summary in summaries),
        candidates=sum(summary.candidates for summary in summaries),
        executed=sum(summary.executed for summary in summaries),
        settled=sum(summary.settled for summary in summaries),
        wins=sum(summary.wins for summary in summaries),
        losses=sum(summary.losses for summary in summaries),
        skipped=sum(summary.skipped for summary in summaries),
        event_count=sum(summary.event_count for summary in summaries),
        artifact_counts=artifact_counts,
        calibration_metrics=calibration,
        brier_metrics=brier,
        failure_categories=failure_categories,
    )


def list_run_ids(
    persistence: JsonlPersistence,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit_runs: int = 50,
) -> list[str]:
    start = _date_to_start(date_from)
    end = _date_to_end(date_to)
    rows = persistence.read_all_artifact_records("pipeline_summaries")

    entries: list[tuple[datetime, str]] = []
    for row in rows:
        run_id = row.get("run_id")
        timestamp = _parse_timestamp(row.get("timestamp"))
        if not isinstance(run_id, str) or not run_id.strip():
            continue
        if timestamp is None:
            continue
        if start and timestamp < start:
            continue
        if end and timestamp > end:
            continue
        entries.append((timestamp, run_id))

    entries.sort(key=lambda item: item[0])
    ordered_run_ids: list[str] = []
    seen: set[str] = set()
    for _, run_id in entries:
        if run_id in seen:
            continue
        seen.add(run_id)
        ordered_run_ids.append(run_id)
    if limit_runs > 0:
        return ordered_run_ids[-limit_runs:]
    return ordered_run_ids


def generate_report_markdown(summary: ReplaySummary) -> str:
    started = summary.started_at.isoformat() if summary.started_at else "n/a"
    finished = summary.finished_at.isoformat() if summary.finished_at else "n/a"
    observability = summary.observability
    counters = observability.get("counters", {})
    stage_timings = observability.get("stage_timings_ms", {})
    settlement_queue = observability.get("settlement_queue", {})
    portfolio = observability.get("portfolio", {})
    correlation_id = observability.get("correlation_id", summary.run_id)
    status = observability.get("status", "n/a")

    lines = [
        f"# Dry-Run Report: {summary.run_id}",
        "",
        "## Summary",
        f"- started_at: {started}",
        f"- finished_at: {finished}",
        f"- total_markets: {summary.total_markets}",
        f"- candidates: {summary.candidates}",
        f"- executed: {summary.executed}",
        f"- settled: {summary.settled}",
        f"- wins: {summary.wins}",
        f"- losses: {summary.losses}",
        f"- skipped: {summary.skipped}",
        f"- event_count: {summary.event_count}",
        "",
        "## Artifact Counts",
    ]
    for artifact_type, count in sorted(summary.artifact_counts.items()):
        lines.append(f"- {artifact_type}: {count}")
    lines.append("")
    lines.append("## Observability")
    lines.append(f"- correlation_id: {correlation_id}")
    lines.append(f"- status: {status}")
    lines.append("- counters:")
    if isinstance(counters, Mapping) and counters:
        for key, value in sorted(counters.items()):
            lines.append(f"  - {key}: {value}")
    else:
        lines.append("  - none")
    lines.append("- stage_timings_ms:")
    if isinstance(stage_timings, Mapping) and stage_timings:
        for key, value in sorted(stage_timings.items()):
            lines.append(f"  - {key}: {value}")
    else:
        lines.append("  - none")
    lines.append("- settlement_queue:")
    if isinstance(settlement_queue, Mapping) and settlement_queue:
        for key, value in sorted(settlement_queue.items()):
            lines.append(f"  - {key}: {value}")
    else:
        lines.append("  - none")
    lines.append("- portfolio:")
    if isinstance(portfolio, Mapping) and portfolio:
        for key, value in sorted(portfolio.items()):
            lines.append(f"  - {key}: {value}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("## Notes")
    lines.append("- report generated from persisted JSONL artifacts and audit events")
    lines.append("- no external/live integrations are used in dry-run alpha")
    return "\n".join(lines) + "\n"


def generate_eval_report_markdown(target: ReplaySummary | WindowEvaluation) -> str:
    if isinstance(target, ReplaySummary):
        return _render_run_eval_report(target)
    return _render_window_eval_report(target)


def write_report(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _latest_summary_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    latest_payload: Mapping[str, Any] = {}
    latest_ts: datetime | None = None
    for row in rows:
        payload_raw = row.get("payload")
        if not isinstance(payload_raw, Mapping):
            continue
        payload = dict(payload_raw)
        timestamp = _parse_timestamp(row.get("timestamp"))
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
        timestamp = _parse_timestamp(row.get("timestamp"))
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
        timestamp = _parse_timestamp(row.get("timestamp"))
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


def _render_run_eval_report(summary: ReplaySummary) -> str:
    started = summary.started_at.isoformat() if summary.started_at else "n/a"
    finished = summary.finished_at.isoformat() if summary.finished_at else "n/a"
    calibration = summary.calibration_metrics.to_dict()
    brier = summary.brier_metrics.to_dict()

    lines = [
        f"# Evaluation Report: {summary.run_id}",
        "",
        "## Replay",
        f"- started_at: {started}",
        f"- finished_at: {finished}",
        f"- reconstructed_records: {len(summary.reconstructed_records)}",
        f"- executed: {summary.executed}",
        f"- settled: {summary.settled}",
        "",
        "## Calibration Metrics",
        f"- sample_size: {calibration['sample_size']}",
        f"- mean_fair_yes_prob: {calibration['mean_fair_yes_prob']}",
        f"- observed_yes_rate: {calibration['observed_yes_rate']}",
        f"- calibration_gap: {calibration['calibration_gap']}",
        f"- expected_calibration_error: {calibration['expected_calibration_error']}",
        f"- mean_confidence: {calibration['mean_confidence']}",
        f"- selected_side_accuracy: {calibration['selected_side_accuracy']}",
        f"- confidence_accuracy_gap: {calibration['confidence_accuracy_gap']}",
        "",
        "## Brier Metrics",
        f"- sample_size: {brier['sample_size']}",
        f"- fair_yes_brier_score: {brier['fair_yes_brier_score']}",
        f"- confidence_brier_score: {brier['confidence_brier_score']}",
        "",
        "## Failure Categories",
    ]
    for category, count in sorted(summary.failure_categories.items()):
        lines.append(f"- {category}: {count}")
    if not summary.failure_categories:
        lines.append("- none: 0")
    lines.append("")
    lines.append("## Notes")
    lines.append("- failure categories are inferred from risk/execution/settlement/postmortem artifacts")
    lines.append("- brier-style metrics are computed only when prediction and settlement outcomes are available")
    return "\n".join(lines) + "\n"


def _render_window_eval_report(window: WindowEvaluation) -> str:
    date_from = window.date_from.isoformat() if window.date_from else "n/a"
    date_to = window.date_to.isoformat() if window.date_to else "n/a"
    calibration = window.calibration_metrics.to_dict()
    brier = window.brier_metrics.to_dict()

    lines = [
        "# Evaluation Report: Window",
        "",
        "## Scope",
        f"- date_from: {date_from}",
        f"- date_to: {date_to}",
        f"- run_count: {window.run_count}",
        f"- run_ids: {', '.join(window.run_ids) if window.run_ids else 'none'}",
        "",
        "## Aggregate Outcomes",
        f"- total_markets: {window.total_markets}",
        f"- candidates: {window.candidates}",
        f"- executed: {window.executed}",
        f"- settled: {window.settled}",
        f"- wins: {window.wins}",
        f"- losses: {window.losses}",
        f"- skipped: {window.skipped}",
        "",
        "## Calibration Metrics",
        f"- sample_size: {calibration['sample_size']}",
        f"- mean_fair_yes_prob: {calibration['mean_fair_yes_prob']}",
        f"- observed_yes_rate: {calibration['observed_yes_rate']}",
        f"- calibration_gap: {calibration['calibration_gap']}",
        f"- expected_calibration_error: {calibration['expected_calibration_error']}",
        f"- mean_confidence: {calibration['mean_confidence']}",
        f"- selected_side_accuracy: {calibration['selected_side_accuracy']}",
        f"- confidence_accuracy_gap: {calibration['confidence_accuracy_gap']}",
        "",
        "## Brier Metrics",
        f"- sample_size: {brier['sample_size']}",
        f"- fair_yes_brier_score: {brier['fair_yes_brier_score']}",
        f"- confidence_brier_score: {brier['confidence_brier_score']}",
        "",
        "## Failure Categories",
    ]
    for category, count in sorted(window.failure_categories.items()):
        lines.append(f"- {category}: {count}")
    if not window.failure_categories:
        lines.append("- none: 0")
    lines.append("")
    lines.append("## Notes")
    lines.append("- this report aggregates replayed runs from persisted artifacts in the selected window")
    return "\n".join(lines) + "\n"


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
        timestamp = _parse_timestamp(row.get("timestamp"))
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


def _aggregate_calibration(metrics: Sequence[CalibrationMetrics]) -> CalibrationMetrics:
    total = sum(item.sample_size for item in metrics)
    if total <= 0:
        return CalibrationMetrics.empty()

    def weighted(attr: str) -> float:
        return sum(getattr(item, attr) * item.sample_size for item in metrics) / total

    mean_fair_yes_prob = weighted("mean_fair_yes_prob")
    observed_yes_rate = weighted("observed_yes_rate")
    mean_confidence = weighted("mean_confidence")
    selected_side_accuracy = weighted("selected_side_accuracy")
    return CalibrationMetrics(
        sample_size=total,
        mean_fair_yes_prob=mean_fair_yes_prob,
        observed_yes_rate=observed_yes_rate,
        calibration_gap=abs(mean_fair_yes_prob - observed_yes_rate),
        expected_calibration_error=weighted("expected_calibration_error"),
        mean_confidence=mean_confidence,
        selected_side_accuracy=selected_side_accuracy,
        confidence_accuracy_gap=abs(mean_confidence - selected_side_accuracy),
    )


def _aggregate_brier(metrics: Sequence[BrierMetrics]) -> BrierMetrics:
    weighted_sample = sum(item.sample_size for item in metrics if item.sample_size > 0)
    if weighted_sample <= 0:
        return BrierMetrics.empty()

    fair_sum = 0.0
    confidence_sum = 0.0
    fair_weight = 0
    confidence_weight = 0
    for item in metrics:
        if item.sample_size <= 0:
            continue
        if item.fair_yes_brier_score is not None:
            fair_sum += item.fair_yes_brier_score * item.sample_size
            fair_weight += item.sample_size
        if item.confidence_brier_score is not None:
            confidence_sum += item.confidence_brier_score * item.sample_size
            confidence_weight += item.sample_size
    return BrierMetrics(
        sample_size=weighted_sample,
        fair_yes_brier_score=(fair_sum / fair_weight) if fair_weight > 0 else None,
        confidence_brier_score=(confidence_sum / confidence_weight) if confidence_weight > 0 else None,
    )


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
    timestamps = [_parse_timestamp(row.get("timestamp")) for row in events]
    valid = [item for item in timestamps if item is not None]
    if not valid:
        return None, None
    return min(valid), max(valid)


def _artifact_time_bounds(artifact_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> tuple[datetime | None, datetime | None]:
    timestamps: list[datetime] = []
    for rows in artifact_rows.values():
        for row in rows:
            parsed = _parse_timestamp(row.get("timestamp"))
            if parsed is not None:
                timestamps.append(parsed)
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _date_to_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_to_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
