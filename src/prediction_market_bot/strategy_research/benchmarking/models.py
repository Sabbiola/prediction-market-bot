from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


def parse_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


@dataclass(slots=True, frozen=True)
class LabelRecord:
    row_id: str
    market_id: str
    event_id: str
    category: str
    market_title: str
    decision_timestamp_utc: datetime
    resolved_at_utc: datetime | None
    resolved_outcome: str
    label_yes: int
    market_yes_prob_at_decision: float
    liquidity_usd: float
    volume_24h_usd: float
    structure_momentum: float
    structure_score: float
    scan_score: float
    research_weighted_sentiment: float
    research_evidence_strength: float
    research_disagreement_score: float
    research_findings_count: int
    data_quality_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "market_id": self.market_id,
            "event_id": self.event_id,
            "category": self.category,
            "market_title": self.market_title,
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "resolved_at_utc": self.resolved_at_utc.isoformat() if self.resolved_at_utc is not None else None,
            "resolved_outcome": self.resolved_outcome,
            "label_yes": self.label_yes,
            "market_yes_prob_at_decision": self.market_yes_prob_at_decision,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h_usd": self.volume_24h_usd,
            "structure_momentum": self.structure_momentum,
            "structure_score": self.structure_score,
            "scan_score": self.scan_score,
            "research_weighted_sentiment": self.research_weighted_sentiment,
            "research_evidence_strength": self.research_evidence_strength,
            "research_disagreement_score": self.research_disagreement_score,
            "research_findings_count": self.research_findings_count,
            "data_quality_flags": list(self.data_quality_flags),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LabelRecord":
        decision_ts = parse_datetime_utc(payload.get("decision_timestamp_utc"))
        if decision_ts is None:
            raise ValueError("label row missing decision_timestamp_utc")
        resolved_ts = parse_datetime_utc(payload.get("resolved_at_utc"))
        row_id = str(payload.get("row_id") or "").strip()
        market_id = str(payload.get("market_id") or "").strip()
        if not row_id or not market_id:
            raise ValueError("label row missing row_id or market_id")
        flags_raw = payload.get("data_quality_flags")
        flags: list[str] = []
        if isinstance(flags_raw, list):
            for row in flags_raw:
                if isinstance(row, str) and row.strip():
                    flags.append(row.strip())
        return cls(
            row_id=row_id,
            market_id=market_id,
            event_id=str(payload.get("event_id") or "").strip(),
            category=str(payload.get("category") or "").strip(),
            market_title=str(payload.get("market_title") or "").strip(),
            decision_timestamp_utc=decision_ts,
            resolved_at_utc=resolved_ts,
            resolved_outcome=str(payload.get("resolved_outcome") or "").strip().upper(),
            label_yes=int(payload.get("label_yes") or 0),
            market_yes_prob_at_decision=float(payload.get("market_yes_prob_at_decision") or 0.5),
            liquidity_usd=float(payload.get("liquidity_usd") or 0.0),
            volume_24h_usd=float(payload.get("volume_24h_usd") or 0.0),
            structure_momentum=float(payload.get("structure_momentum") or 0.0),
            structure_score=float(payload.get("structure_score") or 0.0),
            scan_score=float(payload.get("scan_score") or 0.0),
            research_weighted_sentiment=float(payload.get("research_weighted_sentiment") or 0.0),
            research_evidence_strength=float(payload.get("research_evidence_strength") or 0.0),
            research_disagreement_score=float(payload.get("research_disagreement_score") or 0.0),
            research_findings_count=int(payload.get("research_findings_count") or 0),
            data_quality_flags=tuple(sorted(set(flags))),
        )


@dataclass(slots=True, frozen=True)
class LabelBuildSummary:
    dataset_id: str
    labels_path: Path
    labels_manifest_path: Path
    total_market_rows: int
    labels_written: int
    skipped_missing_timestamp: int
    skipped_unresolved_or_ambiguous: int
    duplicate_rows: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "labels_path": str(self.labels_path),
            "labels_manifest_path": str(self.labels_manifest_path),
            "total_market_rows": self.total_market_rows,
            "labels_written": self.labels_written,
            "skipped_missing_timestamp": self.skipped_missing_timestamp,
            "skipped_unresolved_or_ambiguous": self.skipped_unresolved_or_ambiguous,
            "duplicate_rows": self.duplicate_rows,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class BenchmarkMetrics:
    sample_count: int
    brier_score: float
    log_loss: float
    calibration_error: float
    approval_rate: float
    approved_count: int
    expected_edge_mean: float
    expected_edge_total: float
    realized_edge_mean: float
    realized_edge_total: float
    edge_capture_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "calibration_error": self.calibration_error,
            "approval_rate": self.approval_rate,
            "approved_count": self.approved_count,
            "expected_edge_mean": self.expected_edge_mean,
            "expected_edge_total": self.expected_edge_total,
            "realized_edge_mean": self.realized_edge_mean,
            "realized_edge_total": self.realized_edge_total,
            "edge_capture_ratio": self.edge_capture_ratio,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BenchmarkMetrics":
        return cls(
            sample_count=int(payload.get("sample_count") or 0),
            brier_score=float(payload.get("brier_score") or 0.0),
            log_loss=float(payload.get("log_loss") or 0.0),
            calibration_error=float(payload.get("calibration_error") or 0.0),
            approval_rate=float(payload.get("approval_rate") or 0.0),
            approved_count=int(payload.get("approved_count") or 0),
            expected_edge_mean=float(payload.get("expected_edge_mean") or 0.0),
            expected_edge_total=float(payload.get("expected_edge_total") or 0.0),
            realized_edge_mean=float(payload.get("realized_edge_mean") or 0.0),
            realized_edge_total=float(payload.get("realized_edge_total") or 0.0),
            edge_capture_ratio=float(payload.get("edge_capture_ratio") or 0.0),
        )


@dataclass(slots=True, frozen=True)
class BenchmarkRunSummary:
    dataset_id: str
    run_id: str
    run_path: Path
    split_mode: str
    min_confidence: float
    min_edge_bps: int
    folds: int
    aggregate: Mapping[str, Mapping[str, BenchmarkMetrics]]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        aggregate_payload: dict[str, dict[str, Any]] = {}
        for split_name, split_metrics in self.aggregate.items():
            aggregate_payload[split_name] = {
                baseline: metrics.to_dict()
                for baseline, metrics in split_metrics.items()
            }
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "split_mode": self.split_mode,
            "min_confidence": self.min_confidence,
            "min_edge_bps": self.min_edge_bps,
            "folds": self.folds,
            "aggregate": aggregate_payload,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class BenchmarkComparisonSummary:
    dataset_id: str
    run_a: str
    run_b: str
    split: str
    baseline_deltas: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_a": self.run_a,
            "run_b": self.run_b,
            "split": self.split,
            "baseline_deltas": {
                baseline: dict(values)
                for baseline, values in self.baseline_deltas.items()
            },
        }


@dataclass(slots=True, frozen=True)
class AblationRunSummary:
    dataset_id: str
    run_id: str
    run_path: Path
    report_path: Path
    split_mode: str
    min_confidence: float
    min_edge_bps: int
    folds: int
    variants: tuple[str, ...]
    aggregate: Mapping[str, Mapping[str, Mapping[str, float | int]]]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        aggregate_payload: dict[str, dict[str, dict[str, float | int]]] = {}
        for split_name, variant_payload in self.aggregate.items():
            aggregate_payload[split_name] = {
                variant_name: dict(metrics_payload)
                for variant_name, metrics_payload in variant_payload.items()
            }
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "report_path": str(self.report_path),
            "split_mode": self.split_mode,
            "min_confidence": self.min_confidence,
            "min_edge_bps": self.min_edge_bps,
            "folds": self.folds,
            "variants": list(self.variants),
            "aggregate": aggregate_payload,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class AltDataVariantComparisonSummary:
    dataset_id: str
    run_id: str
    split: str
    reference_variant: str
    report_path: Path
    ranking: tuple[str, ...]
    variant_deltas: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "split": self.split,
            "reference_variant": self.reference_variant,
            "report_path": str(self.report_path),
            "ranking": list(self.ranking),
            "variant_deltas": {
                variant: dict(values)
                for variant, values in self.variant_deltas.items()
            },
        }
