from __future__ import annotations

import math
from typing import Sequence

from .baselines import BaselinePrediction
from .models import BenchmarkMetrics, clamp


def evaluate_predictions(
    predictions: Sequence[BaselinePrediction],
    *,
    min_confidence: float,
    min_edge_probability: float,
) -> BenchmarkMetrics:
    sample_count = len(predictions)
    if sample_count == 0:
        return BenchmarkMetrics(
            sample_count=0,
            brier_score=0.0,
            log_loss=0.0,
            calibration_error=0.0,
            approval_rate=0.0,
            approved_count=0,
            expected_edge_mean=0.0,
            expected_edge_total=0.0,
            realized_edge_mean=0.0,
            realized_edge_total=0.0,
            edge_capture_ratio=0.0,
        )

    eps = 1e-6
    brier_total = 0.0
    log_loss_total = 0.0
    approved_count = 0
    expected_edge_total = 0.0
    realized_edge_total = 0.0

    probabilities: list[float] = []
    labels: list[int] = []

    for item in predictions:
        prob_yes = clamp(item.predicted_yes_prob, lower=eps, upper=1.0 - eps)
        label_yes = int(item.label_yes)
        probabilities.append(prob_yes)
        labels.append(label_yes)

        brier_total += (prob_yes - label_yes) ** 2
        log_loss_total += -((label_yes * math.log(prob_yes)) + ((1 - label_yes) * math.log(1.0 - prob_yes)))

        approved = item.confidence >= min_confidence and item.edge >= min_edge_probability
        if approved:
            approved_count += 1
            expected_edge_total += item.edge
            realized_edge_total += _realized_edge(item=item)

    brier_score = brier_total / sample_count
    log_loss = log_loss_total / sample_count
    calibration_error = _expected_calibration_error(probabilities, labels, bin_count=10)
    approval_rate = approved_count / sample_count
    expected_edge_mean = expected_edge_total / approved_count if approved_count > 0 else 0.0
    realized_edge_mean = realized_edge_total / approved_count if approved_count > 0 else 0.0
    edge_capture_ratio = realized_edge_total / expected_edge_total if abs(expected_edge_total) > eps else 0.0

    return BenchmarkMetrics(
        sample_count=sample_count,
        brier_score=round(brier_score, 8),
        log_loss=round(log_loss, 8),
        calibration_error=round(calibration_error, 8),
        approval_rate=round(approval_rate, 8),
        approved_count=approved_count,
        expected_edge_mean=round(expected_edge_mean, 8),
        expected_edge_total=round(expected_edge_total, 8),
        realized_edge_mean=round(realized_edge_mean, 8),
        realized_edge_total=round(realized_edge_total, 8),
        edge_capture_ratio=round(edge_capture_ratio, 8),
    )


def aggregate_metrics(metrics: Sequence[BenchmarkMetrics]) -> BenchmarkMetrics:
    metrics = tuple(metrics)
    if not metrics:
        return BenchmarkMetrics(
            sample_count=0,
            brier_score=0.0,
            log_loss=0.0,
            calibration_error=0.0,
            approval_rate=0.0,
            approved_count=0,
            expected_edge_mean=0.0,
            expected_edge_total=0.0,
            realized_edge_mean=0.0,
            realized_edge_total=0.0,
            edge_capture_ratio=0.0,
        )
    total_samples = sum(item.sample_count for item in metrics)
    if total_samples <= 0:
        return aggregate_metrics(())

    def _weighted(metric_name: str) -> float:
        weighted_sum = 0.0
        for item in metrics:
            weighted_sum += getattr(item, metric_name) * item.sample_count
        return weighted_sum / total_samples

    approved_count = sum(item.approved_count for item in metrics)
    expected_edge_total = sum(item.expected_edge_total for item in metrics)
    realized_edge_total = sum(item.realized_edge_total for item in metrics)
    expected_edge_mean = expected_edge_total / approved_count if approved_count > 0 else 0.0
    realized_edge_mean = realized_edge_total / approved_count if approved_count > 0 else 0.0
    edge_capture_ratio = realized_edge_total / expected_edge_total if abs(expected_edge_total) > 1e-6 else 0.0
    return BenchmarkMetrics(
        sample_count=total_samples,
        brier_score=round(_weighted("brier_score"), 8),
        log_loss=round(_weighted("log_loss"), 8),
        calibration_error=round(_weighted("calibration_error"), 8),
        approval_rate=round(_weighted("approval_rate"), 8),
        approved_count=approved_count,
        expected_edge_mean=round(expected_edge_mean, 8),
        expected_edge_total=round(expected_edge_total, 8),
        realized_edge_mean=round(realized_edge_mean, 8),
        realized_edge_total=round(realized_edge_total, 8),
        edge_capture_ratio=round(edge_capture_ratio, 8),
    )


def _realized_edge(*, item: BaselinePrediction) -> float:
    if item.selected_side == "YES":
        return float(item.label_yes) - item.market_yes_prob_at_decision
    return item.market_yes_prob_at_decision - float(item.label_yes)


def _expected_calibration_error(probabilities: Sequence[float], labels: Sequence[int], *, bin_count: int) -> float:
    if not probabilities:
        return 0.0
    bin_count = max(bin_count, 1)
    bins: list[list[int]] = [[] for _ in range(bin_count)]
    for idx, prob in enumerate(probabilities):
        bucket = min(int(prob * bin_count), bin_count - 1)
        bins[bucket].append(idx)

    total = len(probabilities)
    ece = 0.0
    for bucket_idxs in bins:
        if not bucket_idxs:
            continue
        bucket_probs = [probabilities[idx] for idx in bucket_idxs]
        bucket_labels = [labels[idx] for idx in bucket_idxs]
        mean_prob = sum(bucket_probs) / len(bucket_probs)
        mean_label = sum(bucket_labels) / len(bucket_labels)
        ece += (len(bucket_idxs) / total) * abs(mean_prob - mean_label)
    return ece
