from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class SimulationThresholds:
    min_confidence: float
    min_edge_probability: float

    @property
    def min_edge_bps(self) -> int:
        return int(round(self.min_edge_probability * 10_000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "min_edge_probability": self.min_edge_probability,
            "min_edge_bps": self.min_edge_bps,
        }


@dataclass(slots=True, frozen=True)
class SimulationAssumptions:
    initial_bankroll_usd: float
    base_position_pct: float
    max_position_pct: float
    min_stake_usd: float
    fee_bps: int
    slippage_bps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_bankroll_usd": self.initial_bankroll_usd,
            "base_position_pct": self.base_position_pct,
            "max_position_pct": self.max_position_pct,
            "min_stake_usd": self.min_stake_usd,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
        }


@dataclass(slots=True, frozen=True)
class DecisionSimulationRecord:
    row_id: str
    market_id: str
    event_id: str
    fold_index: int
    split_name: str
    decision_timestamp_utc: datetime
    resolved_at_utc: datetime | None
    predicted_yes_prob: float
    market_yes_prob_at_decision: float
    selected_side: str
    confidence: float
    expected_edge_probability: float
    label_yes: int
    approved: bool
    approval_reason: str
    stake_usd: float
    fill_price: float
    fee_usd: float
    bankroll_before_usd: float
    bankroll_after_open_usd: float
    settled_at_utc: datetime | None
    payout_usd: float
    pnl_usd: float
    realized_edge_probability: float
    bankroll_after_settlement_usd: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "market_id": self.market_id,
            "event_id": self.event_id,
            "fold_index": self.fold_index,
            "split_name": self.split_name,
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "resolved_at_utc": self.resolved_at_utc.isoformat() if self.resolved_at_utc is not None else None,
            "predicted_yes_prob": self.predicted_yes_prob,
            "market_yes_prob_at_decision": self.market_yes_prob_at_decision,
            "selected_side": self.selected_side,
            "confidence": self.confidence,
            "expected_edge_probability": self.expected_edge_probability,
            "label_yes": self.label_yes,
            "approved": self.approved,
            "approval_reason": self.approval_reason,
            "stake_usd": self.stake_usd,
            "fill_price": self.fill_price,
            "fee_usd": self.fee_usd,
            "bankroll_before_usd": self.bankroll_before_usd,
            "bankroll_after_open_usd": self.bankroll_after_open_usd,
            "settled_at_utc": self.settled_at_utc.isoformat() if self.settled_at_utc is not None else None,
            "payout_usd": self.payout_usd,
            "pnl_usd": self.pnl_usd,
            "realized_edge_probability": self.realized_edge_probability,
            "bankroll_after_settlement_usd": self.bankroll_after_settlement_usd,
        }


@dataclass(slots=True, frozen=True)
class CalibrationCohort:
    bucket: str
    sample_count: int
    mean_predicted_yes_prob: float
    observed_yes_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "sample_count": self.sample_count,
            "mean_predicted_yes_prob": self.mean_predicted_yes_prob,
            "observed_yes_rate": self.observed_yes_rate,
        }


@dataclass(slots=True, frozen=True)
class StrategySimulationMetrics:
    total_predictions: int
    approved_predictions: int
    settled_trades: int
    approval_rate: float
    total_stake_usd: float
    turnover: float
    pnl_usd: float
    roi: float
    final_bankroll_usd: float
    max_drawdown_pct: float
    expected_edge_total: float
    expected_edge_mean: float
    realized_edge_total: float
    realized_edge_mean: float
    edge_capture_ratio: float
    brier_score: float
    log_loss: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_predictions": self.total_predictions,
            "approved_predictions": self.approved_predictions,
            "settled_trades": self.settled_trades,
            "approval_rate": self.approval_rate,
            "total_stake_usd": self.total_stake_usd,
            "turnover": self.turnover,
            "pnl_usd": self.pnl_usd,
            "roi": self.roi,
            "final_bankroll_usd": self.final_bankroll_usd,
            "max_drawdown_pct": self.max_drawdown_pct,
            "expected_edge_total": self.expected_edge_total,
            "expected_edge_mean": self.expected_edge_mean,
            "realized_edge_total": self.realized_edge_total,
            "realized_edge_mean": self.realized_edge_mean,
            "edge_capture_ratio": self.edge_capture_ratio,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
        }


@dataclass(slots=True, frozen=True)
class FoldSimulationSummary:
    fold_index: int
    split_name: str
    train_start_utc: datetime | None
    train_end_utc: datetime | None
    eval_start_utc: datetime | None
    eval_end_utc: datetime | None
    metrics: StrategySimulationMetrics
    decisions: tuple[DecisionSimulationRecord, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "split_name": self.split_name,
            "train_start_utc": self.train_start_utc.isoformat() if self.train_start_utc is not None else None,
            "train_end_utc": self.train_end_utc.isoformat() if self.train_end_utc is not None else None,
            "eval_start_utc": self.eval_start_utc.isoformat() if self.eval_start_utc is not None else None,
            "eval_end_utc": self.eval_end_utc.isoformat() if self.eval_end_utc is not None else None,
            "metrics": self.metrics.to_dict(),
            "decisions": [row.to_dict() for row in self.decisions],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class WalkForwardSimulationSummary:
    dataset_id: str
    run_id: str
    run_path: Path
    baseline_name: str
    eval_split: str
    thresholds: SimulationThresholds
    assumptions: SimulationAssumptions
    folds: tuple[FoldSimulationSummary, ...]
    aggregate_metrics: StrategySimulationMetrics
    calibration_by_cohort: tuple[CalibrationCohort, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "baseline_name": self.baseline_name,
            "eval_split": self.eval_split,
            "thresholds": self.thresholds.to_dict(),
            "assumptions": self.assumptions.to_dict(),
            "folds": [fold.to_dict() for fold in self.folds],
            "aggregate_metrics": self.aggregate_metrics.to_dict(),
            "calibration_by_cohort": [row.to_dict() for row in self.calibration_by_cohort],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class StrategyReportSummary:
    dataset_id: str
    run_id: str
    report_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "report_path": str(self.report_path),
        }
