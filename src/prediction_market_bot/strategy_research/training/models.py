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
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True, frozen=True)
class TrainingFeatureRow:
    row_id: str
    market_id: str
    event_id: str
    category: str
    decision_timestamp_utc: datetime
    label_yes: int
    features: Mapping[str, float]


@dataclass(slots=True, frozen=True)
class SplitRows:
    name: str
    rows: tuple[TrainingFeatureRow, ...]


@dataclass(slots=True, frozen=True)
class HoldoutSplits:
    train: SplitRows
    validation: SplitRows
    test: SplitRows


@dataclass(slots=True, frozen=True)
class OfflineMetrics:
    sample_count: int
    brier_score: float
    log_loss: float
    calibration_error: float
    accuracy: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "calibration_error": self.calibration_error,
            "accuracy": self.accuracy,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OfflineMetrics":
        return cls(
            sample_count=int(payload.get("sample_count") or 0),
            brier_score=float(payload.get("brier_score") or 0.0),
            log_loss=float(payload.get("log_loss") or 0.0),
            calibration_error=float(payload.get("calibration_error") or 0.0),
            accuracy=float(payload.get("accuracy") or 0.0),
        )


@dataclass(slots=True, frozen=True)
class ModelTrainingResult:
    model_name: str
    status: str
    reason: str
    metrics_by_split: Mapping[str, OfflineMetrics]
    window_metrics: Mapping[str, Mapping[str, OfflineMetrics]]
    feature_importance: tuple[tuple[str, float], ...]
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "status": self.status,
            "reason": self.reason,
            "metrics_by_split": {name: metrics.to_dict() for name, metrics in self.metrics_by_split.items()},
            "window_metrics": {
                split_name: {
                    window: metrics.to_dict()
                    for window, metrics in windows.items()
                }
                for split_name, windows in self.window_metrics.items()
            },
            "feature_importance": [
                {"feature": feature, "importance": score}
                for feature, score in self.feature_importance
            ],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelTrainingResult":
        metrics_payload = payload.get("metrics_by_split")
        metrics_by_split: dict[str, OfflineMetrics] = {}
        if isinstance(metrics_payload, Mapping):
            for split_name, metrics in metrics_payload.items():
                if isinstance(metrics, Mapping):
                    metrics_by_split[str(split_name)] = OfflineMetrics.from_dict(metrics)
        windows_payload = payload.get("window_metrics")
        window_metrics: dict[str, dict[str, OfflineMetrics]] = {}
        if isinstance(windows_payload, Mapping):
            for split_name, split_windows in windows_payload.items():
                if not isinstance(split_windows, Mapping):
                    continue
                parsed_windows: dict[str, OfflineMetrics] = {}
                for window, metrics in split_windows.items():
                    if isinstance(metrics, Mapping):
                        parsed_windows[str(window)] = OfflineMetrics.from_dict(metrics)
                window_metrics[str(split_name)] = parsed_windows
        importance_payload = payload.get("feature_importance")
        importance: list[tuple[str, float]] = []
        if isinstance(importance_payload, list):
            for row in importance_payload:
                if not isinstance(row, Mapping):
                    continue
                feature = str(row.get("feature") or "").strip()
                if not feature:
                    continue
                score = float(row.get("importance") or 0.0)
                importance.append((feature, score))
        metadata = payload.get("metadata")
        metadata_map = dict(metadata) if isinstance(metadata, Mapping) else {}
        return cls(
            model_name=str(payload.get("model_name") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            reason=str(payload.get("reason") or "").strip(),
            metrics_by_split=metrics_by_split,
            window_metrics=window_metrics,
            feature_importance=tuple(importance),
            metadata=metadata_map,
        )


@dataclass(slots=True, frozen=True)
class TrainingRunSummary:
    dataset_id: str
    run_id: str
    run_path: Path
    split_mode: str
    models: Mapping[str, ModelTrainingResult]
    best_model: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "run_path": str(self.run_path),
            "split_mode": self.split_mode,
            "models": {
                name: result.to_dict()
                for name, result in self.models.items()
            },
            "best_model": self.best_model,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingRunSummary":
        models_payload = payload.get("models")
        models: dict[str, ModelTrainingResult] = {}
        if isinstance(models_payload, Mapping):
            for name, row in models_payload.items():
                if isinstance(row, Mapping):
                    models[str(name)] = ModelTrainingResult.from_dict(row)
        return cls(
            dataset_id=str(payload.get("dataset_id") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            run_path=Path(str(payload.get("run_path") or "")),
            split_mode=str(payload.get("split_mode") or "holdout").strip(),
            models=models,
            best_model=str(payload.get("best_model") or "").strip(),
            warnings=tuple(str(item) for item in (payload.get("warnings") or []) if isinstance(item, str)),
        )


@dataclass(slots=True, frozen=True)
class CalibrationRunSummary:
    dataset_id: str
    run_id: str
    train_run_id: str
    model_name: str
    method: str
    fit_split: str
    eval_split: str
    raw_metrics: OfflineMetrics
    calibrated_metrics: OfflineMetrics
    calibration_curve_before: tuple[Mapping[str, Any], ...]
    calibration_curve_after: tuple[Mapping[str, Any], ...]
    calibration_path: Path
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "train_run_id": self.train_run_id,
            "model_name": self.model_name,
            "method": self.method,
            "fit_split": self.fit_split,
            "eval_split": self.eval_split,
            "raw_metrics": self.raw_metrics.to_dict(),
            "calibrated_metrics": self.calibrated_metrics.to_dict(),
            "calibration_curve_before": [dict(row) for row in self.calibration_curve_before],
            "calibration_curve_after": [dict(row) for row in self.calibration_curve_after],
            "calibration_path": str(self.calibration_path),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CalibrationRunSummary":
        raw_metrics = payload.get("raw_metrics")
        calibrated = payload.get("calibrated_metrics")
        return cls(
            dataset_id=str(payload.get("dataset_id") or "").strip(),
            run_id=str(payload.get("run_id") or "").strip(),
            train_run_id=str(payload.get("train_run_id") or "").strip(),
            model_name=str(payload.get("model_name") or "").strip(),
            method=str(payload.get("method") or "").strip(),
            fit_split=str(payload.get("fit_split") or "").strip(),
            eval_split=str(payload.get("eval_split") or "").strip(),
            raw_metrics=OfflineMetrics.from_dict(raw_metrics if isinstance(raw_metrics, Mapping) else {}),
            calibrated_metrics=OfflineMetrics.from_dict(calibrated if isinstance(calibrated, Mapping) else {}),
            calibration_curve_before=tuple(
                dict(row) for row in (payload.get("calibration_curve_before") or []) if isinstance(row, Mapping)
            ),
            calibration_curve_after=tuple(
                dict(row) for row in (payload.get("calibration_curve_after") or []) if isinstance(row, Mapping)
            ),
            calibration_path=Path(str(payload.get("calibration_path") or "")),
            warnings=tuple(str(item) for item in (payload.get("warnings") or []) if isinstance(item, str)),
        )


@dataclass(slots=True, frozen=True)
class ModelComparisonSummary:
    dataset_id: str
    run_id: str
    split: str
    ranking: tuple[tuple[str, OfflineMetrics], ...]
    deltas: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "split": self.split,
            "ranking": [
                {
                    "model_name": model_name,
                    "metrics": metrics.to_dict(),
                }
                for model_name, metrics in self.ranking
            ],
            "deltas": {key: dict(value) for key, value in self.deltas.items()},
        }


@dataclass(slots=True, frozen=True)
class ModelCardSummary:
    dataset_id: str
    run_id: str
    model_name: str
    output_path: Path
    decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "model_name": self.model_name,
            "output_path": str(self.output_path),
            "decision": self.decision,
        }
