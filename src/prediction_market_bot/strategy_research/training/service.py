from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from prediction_market_bot.strategy_research.features.schema import FEATURE_SCHEMA_VERSION

from .algorithms import (
    ModelAlgorithm,
    train_logistic_regression,
    train_tree_baseline,
    train_xgboost_candidate,
)
from .calibration import clamp_probability, fit_isotonic_calibrator, fit_platt_calibrator
from .models import (
    CalibrationRunSummary,
    HoldoutSplits,
    ModelCardSummary,
    ModelComparisonSummary,
    ModelTrainingResult,
    OfflineMetrics,
    TrainingFeatureRow,
    TrainingRunSummary,
    parse_datetime_utc,
)
from .splits import build_holdout_splits, month_window_key, temporal_leakage_errors
from .storage import TrainingLabLayout, TrainingLabStorage

_FEATURE_PREFIX = "f_"
_MODEL_LOGISTIC = "logistic_regression_baseline"
_MODEL_TREE = "tree_baseline"
_MODEL_XGBOOST = "xgboost_candidate"
_DECISION_VALUES = {"approved", "rejected", "needs_more_evidence"}


@dataclass(slots=True, frozen=True)
class _PredictionRecord:
    row: TrainingFeatureRow
    probability: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row.row_id,
            "market_id": self.row.market_id,
            "event_id": self.row.event_id,
            "category": self.row.category,
            "decision_timestamp_utc": self.row.decision_timestamp_utc.isoformat(),
            "label_yes": self.row.label_yes,
            "probability": self.probability,
        }


class StrategyTrainingLabService:
    def __init__(
        self,
        *,
        historical_base_dir: Path,
        default_dataset_id: str,
        model_card_template_path: Path | None = None,
    ) -> None:
        self.historical_base_dir = historical_base_dir
        self.default_dataset_id = default_dataset_id
        self.model_card_template_path = model_card_template_path or Path("docs/MODEL_CARD_TEMPLATE.md")

    def train_baseline_models(
        self,
        *,
        dataset_id: str | None = None,
        feature_rows_path: Path | None = None,
        split_mode: str = "holdout",
        train_ratio: float = 0.6,
        validation_ratio: float = 0.2,
        window_days: int = 30,
        include_xgboost: bool = True,
    ) -> TrainingRunSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        storage = self._storage(effective_dataset_id)
        rows_path = (
            feature_rows_path
            if feature_rows_path is not None
            else self.historical_base_dir
            / effective_dataset_id
            / "derived"
            / "feature_store"
            / "v1"
            / "feature_rows.jsonl"
        )
        rows = self._load_feature_rows(rows_path)
        if not rows:
            raise ValueError(f"No feature rows found at {rows_path}. Run build-feature-dataset first.")
        normalized_split_mode = split_mode.strip().lower()
        if normalized_split_mode != "holdout":
            raise ValueError("split_mode must be holdout for train-baseline-models.")
        splits = build_holdout_splits(rows, train_ratio=train_ratio, validation_ratio=validation_ratio)
        split_errors = temporal_leakage_errors(splits)
        if split_errors:
            raise ValueError("; ".join(split_errors))

        feature_names = self._feature_names(rows)
        if not feature_names:
            raise ValueError("No numeric feature columns found (expected f_* columns).")

        run_id = storage.new_run_id()
        run_dir = storage.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        model_artifacts: dict[str, dict[str, Any]] = {}

        models: dict[str, ModelTrainingResult] = {}
        models[_MODEL_LOGISTIC] = self._train_model(
            model_name=_MODEL_LOGISTIC,
            model=train_logistic_regression(splits.train.rows, feature_names),
            splits=splits,
            feature_names=feature_names,
            run_id=run_id,
            run_dir=run_dir,
            window_days=window_days,
            model_artifacts=model_artifacts,
        )
        models[_MODEL_TREE] = self._train_model(
            model_name=_MODEL_TREE,
            model=train_tree_baseline(splits.train.rows, feature_names),
            splits=splits,
            feature_names=feature_names,
            run_id=run_id,
            run_dir=run_dir,
            window_days=window_days,
            model_artifacts=model_artifacts,
        )
        if include_xgboost:
            xgboost_model, reason = train_xgboost_candidate(splits.train.rows, feature_names)
            if xgboost_model is None:
                warnings.append(reason)
                models[_MODEL_XGBOOST] = ModelTrainingResult(
                    model_name=_MODEL_XGBOOST,
                    status="skipped",
                    reason=reason,
                    metrics_by_split={},
                    window_metrics={},
                    feature_importance=(),
                    metadata={"algorithm": "xgboost_candidate"},
                )
            else:
                models[_MODEL_XGBOOST] = self._train_model(
                    model_name=_MODEL_XGBOOST,
                    model=xgboost_model,
                    splits=splits,
                    feature_names=feature_names,
                    run_id=run_id,
                    run_dir=run_dir,
                    window_days=window_days,
                    model_artifacts=model_artifacts,
                )

        best_model = self._pick_best_model(models)
        summary = TrainingRunSummary(
            dataset_id=effective_dataset_id,
            run_id=run_id,
            run_path=run_dir / "summary.json",
            split_mode=normalized_split_mode,
            models=models,
            best_model=best_model,
            warnings=tuple(warnings),
        )
        summary_payload = {
            **summary.to_dict(),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "feature_rows_path": str(rows_path),
            "feature_columns": list(feature_names),
            "split_parameters": {
                "train_ratio": train_ratio,
                "validation_ratio": validation_ratio,
                "window_days": max(window_days, 1),
            },
            "model_artifacts": model_artifacts,
        }
        storage.write_json(run_dir / "summary.json", summary_payload)
        storage.write_latest_training(summary_payload)
        return summary

    def calibrate_model(
        self,
        *,
        dataset_id: str | None = None,
        run_id: str | None = None,
        model_name: str | None = None,
        method: str = "platt",
        fit_split: str = "validation",
        eval_split: str = "test",
    ) -> CalibrationRunSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        storage = self._storage(effective_dataset_id)
        effective_run_id = (run_id or storage.latest_training_run_id() or "").strip()
        if not effective_run_id:
            raise ValueError("No training run available. Run train-baseline-models first.")
        train_payload = storage.read_json(storage.run_dir(effective_run_id) / "summary.json")
        if not train_payload:
            raise FileNotFoundError(f"Training run summary not found for run_id={effective_run_id}")
        train_summary = TrainingRunSummary.from_dict(train_payload)
        effective_model_name = (model_name or train_summary.best_model).strip() or train_summary.best_model
        if effective_model_name not in train_summary.models:
            raise ValueError(f"Model {effective_model_name!r} not found in run {effective_run_id}.")
        selected_result = train_summary.models[effective_model_name]
        if selected_result.status != "trained":
            raise ValueError(
                f"Model {effective_model_name!r} is not trainable for calibration "
                f"(status={selected_result.status}, reason={selected_result.reason})."
            )

        normalized_fit_split = self._normalize_split_name(fit_split)
        normalized_eval_split = self._normalize_split_name(eval_split)
        fit_predictions = self._read_prediction_records(
            storage.run_dir(effective_run_id) / "predictions" / f"predictions_{effective_model_name}_{normalized_fit_split}.jsonl"
        )
        eval_predictions = self._read_prediction_records(
            storage.run_dir(effective_run_id) / "predictions" / f"predictions_{effective_model_name}_{normalized_eval_split}.jsonl"
        )
        if not fit_predictions:
            raise ValueError(f"No predictions available for fit split {normalized_fit_split}.")
        if not eval_predictions:
            raise ValueError(f"No predictions available for eval split {normalized_eval_split}.")

        probabilities_fit = [record.probability for record in fit_predictions]
        labels_fit = [record.row.label_yes for record in fit_predictions]
        probabilities_eval = [record.probability for record in eval_predictions]
        labels_eval = [record.row.label_yes for record in eval_predictions]

        normalized_method = method.strip().lower()
        if normalized_method == "platt":
            platt_calibrator = fit_platt_calibrator(probabilities_fit, labels_fit)
            calibrator_payload: dict[str, Any] = {
                "artifact_type": "prediction_calibration_v2",
                "artifact_version": "v1",
                "calibration_version": "",
                "model_version": effective_run_id,
                "model_name": effective_model_name,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "method": "platt",
                "parameters": platt_calibrator.to_dict(),
            }
            calibrated_eval = [platt_calibrator.predict(probability) for probability in probabilities_eval]
        elif normalized_method == "isotonic":
            isotonic_calibrator = fit_isotonic_calibrator(probabilities_fit, labels_fit)
            calibrator_payload = {
                "artifact_type": "prediction_calibration_v2",
                "artifact_version": "v1",
                "calibration_version": "",
                "model_version": effective_run_id,
                "model_name": effective_model_name,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "method": "isotonic",
                "parameters": isotonic_calibrator.to_dict(),
            }
            calibrated_eval = [isotonic_calibrator.predict(probability) for probability in probabilities_eval]
        else:
            raise ValueError("Unsupported calibration method. Use platt or isotonic.")

        raw_metrics = self._evaluate_metric_vectors(probabilities_eval, labels_eval)
        calibrated_metrics = self._evaluate_metric_vectors(calibrated_eval, labels_eval)
        warnings: list[str] = []
        if calibrated_metrics.brier_score > raw_metrics.brier_score:
            warnings.append("calibration increased brier score on evaluation split")
        if calibrated_metrics.log_loss > raw_metrics.log_loss:
            warnings.append("calibration increased log loss on evaluation split")

        run_id_calibration = storage.new_calibration_id()
        calibrator_payload["calibration_version"] = run_id_calibration
        calibration_dir = storage.calibration_dir(run_id_calibration)
        calibration_dir.mkdir(parents=True, exist_ok=True)
        calibration_path = storage.write_json(calibration_dir / "calibration.json", calibrator_payload)
        storage.write_jsonl(
            calibration_dir / "eval_predictions_calibrated.jsonl",
            [
                {
                    **record.to_dict(),
                    "probability_calibrated": probability,
                }
                for record, probability in zip(eval_predictions, calibrated_eval, strict=False)
            ],
        )

        summary = CalibrationRunSummary(
            dataset_id=effective_dataset_id,
            run_id=run_id_calibration,
            train_run_id=effective_run_id,
            model_name=effective_model_name,
            method=normalized_method,
            fit_split=normalized_fit_split,
            eval_split=normalized_eval_split,
            raw_metrics=raw_metrics,
            calibrated_metrics=calibrated_metrics,
            calibration_curve_before=self._calibration_curve(probabilities_eval, labels_eval),
            calibration_curve_after=self._calibration_curve(calibrated_eval, labels_eval),
            calibration_path=calibration_path,
            warnings=tuple(warnings),
        )
        summary_payload = {
            **summary.to_dict(),
            "created_at_utc": datetime.now(UTC).isoformat(),
        }
        storage.write_json(calibration_dir / "summary.json", summary_payload)
        storage.write_latest_calibration(summary_payload)
        return summary

    def compare_models(
        self,
        *,
        dataset_id: str | None = None,
        run_id: str | None = None,
        split: str = "test",
    ) -> ModelComparisonSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        storage = self._storage(effective_dataset_id)
        effective_run_id = (run_id or storage.latest_training_run_id() or "").strip()
        if not effective_run_id:
            raise ValueError("No training run available for comparison.")
        payload = storage.read_json(storage.run_dir(effective_run_id) / "summary.json")
        if not payload:
            raise FileNotFoundError(f"Training run summary not found for run_id={effective_run_id}")
        summary = TrainingRunSummary.from_dict(payload)
        split_name = self._normalize_split_name(split)

        scored: list[tuple[str, OfflineMetrics]] = []
        for model_name, result in summary.models.items():
            metrics = result.metrics_by_split.get(split_name)
            if result.status != "trained" or metrics is None or metrics.sample_count <= 0:
                continue
            scored.append((model_name, metrics))
        if not scored:
            raise ValueError(f"No trained models with metrics on split={split_name}.")
        scored.sort(key=lambda item: (item[1].brier_score, item[1].log_loss, item[1].calibration_error))
        baseline = scored[0][1]
        deltas: dict[str, dict[str, float]] = {}
        for model_name, metrics in scored:
            deltas[model_name] = {
                "delta_brier_score_vs_best": round(metrics.brier_score - baseline.brier_score, 8),
                "delta_log_loss_vs_best": round(metrics.log_loss - baseline.log_loss, 8),
                "delta_calibration_error_vs_best": round(metrics.calibration_error - baseline.calibration_error, 8),
                "delta_accuracy_vs_best": round(metrics.accuracy - baseline.accuracy, 8),
            }
        return ModelComparisonSummary(
            dataset_id=effective_dataset_id,
            run_id=effective_run_id,
            split=split_name,
            ranking=tuple(scored),
            deltas=deltas,
        )

    def generate_model_card(
        self,
        *,
        dataset_id: str | None = None,
        run_id: str | None = None,
        model_name: str | None = None,
        calibration_run_id: str | None = None,
        output_path: Path | None = None,
        owner: str = "strategy-research",
        decision: str = "needs_more_evidence",
    ) -> ModelCardSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        storage = self._storage(effective_dataset_id)
        effective_run_id = (run_id or storage.latest_training_run_id() or "").strip()
        if not effective_run_id:
            raise ValueError("No training run available for model-card generation.")
        train_payload = storage.read_json(storage.run_dir(effective_run_id) / "summary.json")
        if not train_payload:
            raise FileNotFoundError(f"Training run summary not found for run_id={effective_run_id}")
        train_summary = TrainingRunSummary.from_dict(train_payload)
        effective_model_name = (model_name or train_summary.best_model).strip() or train_summary.best_model
        if effective_model_name not in train_summary.models:
            raise ValueError(f"Model {effective_model_name!r} not found in run {effective_run_id}.")

        selected_result = train_summary.models[effective_model_name]
        normalized_decision = decision.strip().lower()
        if normalized_decision not in _DECISION_VALUES:
            raise ValueError("decision must be one of: approved, rejected, needs_more_evidence.")

        calibration_summary = self._resolve_calibration_summary(
            storage=storage,
            requested_run_id=calibration_run_id,
            train_run_id=effective_run_id,
            model_name=effective_model_name,
        )
        card_path = (
            output_path
            if output_path is not None
            else storage.layout.model_cards_dir / f"{effective_model_name}_{effective_run_id}.md"
        )
        card_path.parent.mkdir(parents=True, exist_ok=True)

        rendered = self._render_model_card(
            dataset_id=effective_dataset_id,
            run_id=effective_run_id,
            owner=owner,
            decision=normalized_decision,
            model_name=effective_model_name,
            model_result=selected_result,
            calibration_summary=calibration_summary,
        )
        card_path.write_text(rendered, encoding="utf-8")
        return ModelCardSummary(
            dataset_id=effective_dataset_id,
            run_id=effective_run_id,
            model_name=effective_model_name,
            output_path=card_path,
            decision=normalized_decision,
        )

    def _render_model_card(
        self,
        *,
        dataset_id: str,
        run_id: str,
        owner: str,
        decision: str,
        model_name: str,
        model_result: ModelTrainingResult,
        calibration_summary: CalibrationRunSummary | None,
    ) -> str:
        test_metrics = model_result.metrics_by_split.get("test")
        validation_metrics = model_result.metrics_by_split.get("validation")
        created_at = datetime.now(UTC).isoformat()
        test_metrics_payload = test_metrics.to_dict() if test_metrics is not None else {}
        validation_metrics_payload = validation_metrics.to_dict() if validation_metrics is not None else {}
        calibration_text = "not_calibrated"
        if calibration_summary is not None:
            calibration_text = (
                f"{calibration_summary.method} "
                f"(fit={calibration_summary.fit_split}, eval={calibration_summary.eval_split}, "
                f"raw_brier={calibration_summary.raw_metrics.brier_score:.6f}, "
                f"calibrated_brier={calibration_summary.calibrated_metrics.brier_score:.6f})"
            )
        model_metadata = json.dumps(dict(model_result.metadata), indent=2)
        template_reference = self.model_card_template_path
        return (
            "# Model Card\n\n"
            f"_Generated from template `{template_reference}` at {created_at}_\n\n"
            "## 1. Model identity\n\n"
            f"- model_name: {model_name}\n"
            f"- model_version: {run_id}\n"
            f"- owner: {owner}\n"
            f"- created_at_utc: {created_at}\n"
            "- code_commit_sha: unknown\n\n"
            "## 2. Intended use\n\n"
            "- primary_use_case: offline prediction probability estimation for beta-live promotion gating\n"
            "- supported_runtime_modes: PAPER_LIVE shadow-scoring, SANDBOX_CHAIN shadow-scoring\n"
            "- unsupported_use_cases: live venue order posting\n"
            "- safety_constraints: runtime training disabled; human review and risk guardrails remain mandatory\n\n"
            "## 3. Training data\n\n"
            f"- dataset_id: {dataset_id}\n"
            "- dataset_manifest_path: derived/feature_store/v1/feature_manifest.json\n"
            "- label_definition: label_yes=1 (YES), label_yes=0 (NO)\n"
            "- split_definition: time-based holdout train/validation/test\n"
            "- leakage_checks: temporal split order and row overlap checks enforced\n\n"
            "## 4. Features\n\n"
            "- feature_groups: market, liquidity/spread, volume/activity, time, momentum, category/event, research\n"
            "- feature_freeze_time_rule: features generated at decision_timestamp_utc only\n"
            "- prohibited_features: post-resolution and post-decision evidence\n\n"
            "## 5. Benchmarks\n\n"
            "- benchmark_market_implied: required in separate benchmark suite\n"
            "- benchmark_fifty_fifty: required in separate benchmark suite\n"
            "- benchmark_category_prior: required in separate benchmark suite\n"
            "- benchmark_heuristic_prediction_agent: required in separate benchmark suite\n"
            "- benchmark_research_only: required in separate benchmark suite\n"
            "- benchmark_momentum_structure: required in separate benchmark suite\n\n"
            "## 6. Evaluation metrics\n\n"
            f"- test_metrics: {json.dumps(test_metrics_payload, sort_keys=True)}\n"
            f"- validation_metrics: {json.dumps(validation_metrics_payload, sort_keys=True)}\n"
            f"- calibration_summary: {calibration_text}\n\n"
            "## 7. Promotion evidence\n\n"
            "- stage_reached: Stage A/B (offline)\n"
            "- gate_results: pending benchmark + walk-forward formal review\n"
            "- regression_checks: compare-models output required\n"
            "- operational_impact_summary: runtime unchanged; offline-only artifact generation\n\n"
            "## 8. Risks and limitations\n\n"
            "- known_failure_modes: dataset sparsity, regime shifts, calibration drift\n"
            "- market_regimes_where_model_is_weak: to be validated in walk-forward windows\n"
            "- data_quality_limits: depends on feature completeness flags and source coverage\n"
            "- monitoring_requirements: brier/log-loss drift, calibration error drift, approval-rate drift\n\n"
            "## 9. Deployment decision\n\n"
            f"- decision: {decision}\n"
            f"- decision_date_utc: {created_at}\n"
            "- approvers: pending\n"
            "- rationale: offline training artifact generated; promotion requires explicit gate review\n"
            "- rollback_triggers: benchmark regression, calibration degradation, operational instability\n\n"
            "## 10. Change log\n\n"
            "- previous_version: n/a\n"
            "- what_changed: offline model training/calibration artifact generation\n"
            "- expected_effect: measurable model comparisons with calibrated probabilities\n"
            "- validation_done: train-baseline-models, calibrate-model, compare-models, generate-model-card\n\n"
            "## Appendix: model_metadata\n\n"
            "```json\n"
            f"{model_metadata}\n"
            "```\n"
        )

    @staticmethod
    def _resolve_calibration_summary(
        *,
        storage: TrainingLabStorage,
        requested_run_id: str | None,
        train_run_id: str,
        model_name: str,
    ) -> CalibrationRunSummary | None:
        candidate_ids: list[str] = []
        if requested_run_id:
            candidate_ids.append(requested_run_id)
        latest = storage.latest_calibration_run_id()
        if latest:
            candidate_ids.append(latest)
        for candidate_id in candidate_ids:
            payload = storage.read_json(storage.calibration_dir(candidate_id) / "summary.json")
            if not payload:
                continue
            summary = CalibrationRunSummary.from_dict(payload)
            if summary.train_run_id == train_run_id and summary.model_name == model_name:
                return summary
        return None

    def _train_model(
        self,
        *,
        model_name: str,
        model: ModelAlgorithm,
        splits: HoldoutSplits,
        feature_names: tuple[str, ...],
        run_id: str,
        run_dir: Path,
        window_days: int,
        model_artifacts: dict[str, dict[str, Any]],
    ) -> ModelTrainingResult:
        split_records: dict[str, tuple[_PredictionRecord, ...]] = {}
        metrics_by_split: dict[str, OfflineMetrics] = {}
        window_metrics: dict[str, dict[str, OfflineMetrics]] = {}
        for split_name, rows in {
            "train": splits.train.rows,
            "validation": splits.validation.rows,
            "test": splits.test.rows,
        }.items():
            records = tuple(
                _PredictionRecord(
                    row=row,
                    probability=clamp_probability(model.predict_proba(row)),
                )
                for row in rows
            )
            split_records[split_name] = records
            metrics_by_split[split_name] = self._evaluate_prediction_records(records)
            window_metrics[split_name] = self._window_metrics(records, window_days=window_days)

        model_dir = run_dir / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        predictions_dir = run_dir / "predictions"
        predictions_dir.mkdir(parents=True, exist_ok=True)
        for split_name, records in split_records.items():
            path = predictions_dir / f"predictions_{model_name}_{split_name}.jsonl"
            path.write_text(
                "".join(json.dumps(record.to_dict(), default=str) + "\n" for record in records),
                encoding="utf-8",
            )
        model_path = model_dir / f"{model_name}.json"
        payload = {
            "artifact_type": "prediction_model_v2",
            "artifact_version": "v1",
            "model_name": model_name,
            "model_version": run_id,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "required_features": list(feature_names),
            "algorithm_payload": model.serializable_payload(),
            "metadata": model.metadata(),
            "feature_columns": list(feature_names),
            "metrics_by_split": {
                split: metrics.to_dict() for split, metrics in metrics_by_split.items()
            },
            "feature_importance": [
                {"feature": feature, "importance": score}
                for feature, score in model.feature_importance(top_k=20)
            ],
        }
        model_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        model_artifacts[model_name] = {
            "model_path": str(model_path),
            "prediction_paths": {
                split_name: str(run_dir / "predictions" / f"predictions_{model_name}_{split_name}.jsonl")
                for split_name in split_records.keys()
            },
        }
        return ModelTrainingResult(
            model_name=model_name,
            status="trained",
            reason="",
            metrics_by_split=metrics_by_split,
            window_metrics=window_metrics,
            feature_importance=model.feature_importance(top_k=20),
            metadata=model.metadata(),
        )

    @staticmethod
    def _pick_best_model(models: Mapping[str, ModelTrainingResult]) -> str:
        candidates: list[tuple[str, OfflineMetrics]] = []
        for name, result in models.items():
            if result.status != "trained":
                continue
            metrics = result.metrics_by_split.get("test")
            if metrics is None or metrics.sample_count <= 0:
                metrics = result.metrics_by_split.get("validation")
            if metrics is None or metrics.sample_count <= 0:
                continue
            candidates.append((name, metrics))
        if not candidates:
            return next(iter(models.keys()), "")
        candidates.sort(key=lambda row: (row[1].brier_score, row[1].log_loss, row[1].calibration_error))
        return candidates[0][0]

    @staticmethod
    def _read_prediction_records(path: Path) -> tuple[_PredictionRecord, ...]:
        if not path.exists():
            return ()
        records: list[_PredictionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            raw = json.loads(text)
            if not isinstance(raw, Mapping):
                continue
            timestamp = parse_datetime_utc(raw.get("decision_timestamp_utc"))
            if timestamp is None:
                continue
            probability = float(raw.get("probability") or 0.5)
            probability = clamp_probability(probability)
            row = TrainingFeatureRow(
                row_id=str(raw.get("row_id") or "").strip(),
                market_id=str(raw.get("market_id") or "").strip(),
                event_id=str(raw.get("event_id") or "").strip(),
                category=str(raw.get("category") or "").strip(),
                decision_timestamp_utc=timestamp,
                label_yes=1 if int(raw.get("label_yes") or 0) == 1 else 0,
                features={},
            )
            if not row.row_id:
                continue
            records.append(_PredictionRecord(row=row, probability=probability))
        return tuple(records)

    @staticmethod
    def _normalize_split_name(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"train", "validation", "test"}:
            raise ValueError("split must be one of: train, validation, test")
        return normalized

    @staticmethod
    def _evaluate_prediction_records(records: Sequence[_PredictionRecord]) -> OfflineMetrics:
        probabilities = [record.probability for record in records]
        labels = [record.row.label_yes for record in records]
        return StrategyTrainingLabService._evaluate_metric_vectors(probabilities, labels)

    @staticmethod
    def _evaluate_metric_vectors(probabilities: Sequence[float], labels: Sequence[int]) -> OfflineMetrics:
        sample_count = len(probabilities)
        if sample_count == 0:
            return OfflineMetrics(
                sample_count=0,
                brier_score=0.0,
                log_loss=0.0,
                calibration_error=0.0,
                accuracy=0.0,
            )
        clipped = [clamp_probability(float(value)) for value in probabilities]
        brier = sum((prob - float(label)) ** 2 for prob, label in zip(clipped, labels, strict=False)) / sample_count
        eps = 1e-6
        log_loss = 0.0
        correct = 0
        for probability, label in zip(clipped, labels, strict=False):
            p = min(max(probability, eps), 1.0 - eps)
            y = int(label)
            log_loss += -((y * math.log(p)) + ((1 - y) * math.log(1.0 - p)))
            if (p >= 0.5 and y == 1) or (p < 0.5 and y == 0):
                correct += 1
        log_loss /= sample_count
        calibration_error = StrategyTrainingLabService._calibration_error(clipped, labels, bin_count=10)
        accuracy = correct / sample_count
        return OfflineMetrics(
            sample_count=sample_count,
            brier_score=round(brier, 8),
            log_loss=round(log_loss, 8),
            calibration_error=round(calibration_error, 8),
            accuracy=round(accuracy, 8),
        )

    @staticmethod
    def _calibration_error(probabilities: Sequence[float], labels: Sequence[int], *, bin_count: int) -> float:
        if not probabilities:
            return 0.0
        bins: list[list[int]] = [[] for _ in range(max(bin_count, 1))]
        for index, probability in enumerate(probabilities):
            bucket_index = min(int(probability * len(bins)), len(bins) - 1)
            bins[bucket_index].append(index)
        total = len(probabilities)
        error = 0.0
        for bucket_rows in bins:
            if not bucket_rows:
                continue
            mean_prob = sum(probabilities[idx] for idx in bucket_rows) / len(bucket_rows)
            mean_label = sum(labels[idx] for idx in bucket_rows) / len(bucket_rows)
            error += (len(bucket_rows) / total) * abs(mean_prob - mean_label)
        return error

    @staticmethod
    def _calibration_curve(
        probabilities: Sequence[float],
        labels: Sequence[int],
        *,
        bin_count: int = 10,
    ) -> tuple[dict[str, Any], ...]:
        if not probabilities:
            return ()
        bins: list[list[int]] = [[] for _ in range(max(bin_count, 1))]
        for index, probability in enumerate(probabilities):
            bucket_index = min(int(probability * len(bins)), len(bins) - 1)
            bins[bucket_index].append(index)
        payload: list[dict[str, Any]] = []
        for bucket_index, bucket_rows in enumerate(bins):
            if not bucket_rows:
                continue
            probs = [probabilities[idx] for idx in bucket_rows]
            labs = [labels[idx] for idx in bucket_rows]
            payload.append(
                {
                    "bin_index": bucket_index,
                    "count": len(bucket_rows),
                    "mean_probability": round(sum(probs) / len(probs), 8),
                    "mean_label": round(sum(labs) / len(labs), 8),
                }
            )
        return tuple(payload)

    @staticmethod
    def _window_metrics(records: Sequence[_PredictionRecord], *, window_days: int) -> dict[str, OfflineMetrics]:
        del window_days
        grouped: dict[str, list[_PredictionRecord]] = {}
        for record in records:
            key = month_window_key(record.row)
            grouped.setdefault(key, []).append(record)
        payload: dict[str, OfflineMetrics] = {}
        for key, group in grouped.items():
            payload[key] = StrategyTrainingLabService._evaluate_prediction_records(group)
        return payload

    @staticmethod
    def _feature_names(rows: Sequence[TrainingFeatureRow]) -> tuple[str, ...]:
        names: set[str] = set()
        for row in rows:
            for feature_name in row.features.keys():
                if feature_name.startswith(_FEATURE_PREFIX):
                    names.add(feature_name)
        return tuple(sorted(names))

    def _load_feature_rows(self, path: Path) -> tuple[TrainingFeatureRow, ...]:
        if not path.exists():
            return ()
        rows: list[TrainingFeatureRow] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if not isinstance(raw, Mapping):
                    continue
                row = self._parse_feature_row(raw)
                if row is not None:
                    rows.append(row)
        rows.sort(key=lambda row: (row.decision_timestamp_utc, row.market_id, row.row_id))
        return tuple(rows)

    @staticmethod
    def _parse_feature_row(raw: Mapping[str, Any]) -> TrainingFeatureRow | None:
        row_id = str(raw.get("row_id") or "").strip()
        market_id = str(raw.get("market_id") or "").strip()
        if not row_id or not market_id:
            return None
        decision_timestamp = parse_datetime_utc(raw.get("decision_timestamp_utc"))
        if decision_timestamp is None:
            return None
        label_yes = 1 if int(raw.get("label_yes") or 0) == 1 else 0
        feature_values: dict[str, float] = {}
        for key, value in raw.items():
            key_text = str(key).strip()
            if not key_text.startswith(_FEATURE_PREFIX):
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isnan(numeric) or math.isinf(numeric):
                continue
            feature_values[key_text] = numeric
        return TrainingFeatureRow(
            row_id=row_id,
            market_id=market_id,
            event_id=str(raw.get("event_id") or "").strip(),
            category=str(raw.get("category") or "").strip(),
            decision_timestamp_utc=decision_timestamp,
            label_yes=label_yes,
            features=feature_values,
        )

    def _storage(self, dataset_id: str) -> TrainingLabStorage:
        layout = TrainingLabLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=dataset_id,
        )
        return TrainingLabStorage(layout)
