from __future__ import annotations

from datetime import date
import logging
from time import perf_counter
from typing import Sequence

from prediction_market_bot.infrastructure.persistence import JsonlPersistence

from .history_queries import date_to_end, date_to_start, list_run_ids
from .replay_service import replay_run
from .run_summary_service import BrierMetrics, CalibrationMetrics, WindowEvaluation

logger = logging.getLogger(__name__)


def evaluate_window(
    persistence: JsonlPersistence,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit_runs: int = 50,
) -> WindowEvaluation:
    total_started = perf_counter()
    query_started = perf_counter()
    selected_run_ids = list_run_ids(
        persistence,
        date_from=date_from,
        date_to=date_to,
        limit_runs=limit_runs,
    )
    run_id_query_ms = round(max((perf_counter() - query_started) * 1000.0, 0.0), 3)

    replay_started = perf_counter()
    summaries = [replay_run(persistence, run_id) for run_id in selected_run_ids]
    replay_queries_ms = round(max((perf_counter() - replay_started) * 1000.0, 0.0), 3)

    if not summaries:
        result = WindowEvaluation(
            date_from=date_to_start(date_from),
            date_to=date_to_end(date_to),
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
        logger.info(
            "history_evaluate_window_timing",
            extra={
                "event": "history_evaluate_window_timing",
                "run_count": 0,
                "run_id_query_ms": run_id_query_ms,
                "replay_queries_ms": replay_queries_ms,
                "total_ms": round(max((perf_counter() - total_started) * 1000.0, 0.0), 3),
            },
        )
        return result

    aggregate_started = perf_counter()
    artifact_counts: dict[str, int] = {}
    failure_categories: dict[str, int] = {}
    for summary in summaries:
        for artifact_type, count in summary.artifact_counts.items():
            artifact_counts[artifact_type] = artifact_counts.get(artifact_type, 0) + count
        for category, count in summary.failure_categories.items():
            failure_categories[category] = failure_categories.get(category, 0) + count

    calibration = _aggregate_calibration([summary.calibration_metrics for summary in summaries])
    brier = _aggregate_brier([summary.brier_metrics for summary in summaries])
    aggregate_ms = round(max((perf_counter() - aggregate_started) * 1000.0, 0.0), 3)

    result = WindowEvaluation(
        date_from=date_to_start(date_from),
        date_to=date_to_end(date_to),
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
    logger.info(
        "history_evaluate_window_timing",
        extra={
            "event": "history_evaluate_window_timing",
            "run_count": result.run_count,
            "run_id_query_ms": run_id_query_ms,
            "replay_queries_ms": replay_queries_ms,
            "aggregate_ms": aggregate_ms,
            "total_ms": round(max((perf_counter() - total_started) * 1000.0, 0.0), 3),
        },
    )
    return result


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
