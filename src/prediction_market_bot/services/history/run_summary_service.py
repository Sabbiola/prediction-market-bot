from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

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
