from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .run_summary_service import ReplaySummary, WindowEvaluation


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
