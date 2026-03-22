from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.strategy_research.alt_features import ALT_FEATURE_SCHEMA_VERSION

from .ablation import ABLATION_VARIANT_NAMES, AblationPredictorSuite, AltFeatureRow, load_alt_feature_rows, uses_alt_features
from .baselines import BASELINE_NAMES, BaselinePredictorSuite, fit_training_stats
from .evaluation import aggregate_metrics, evaluate_predictions
from .labels import LabelDatasetBuilder
from .models import (
    AblationRunSummary,
    AltDataVariantComparisonSummary,
    BenchmarkComparisonSummary,
    BenchmarkMetrics,
    BenchmarkRunSummary,
    LabelBuildSummary,
    LabelRecord,
)
from .splits import build_holdout_fold, build_walk_forward_folds, temporal_leakage_errors


@dataclass(slots=True, frozen=True)
class BenchmarkDatasetLayout:
    dataset_id: str
    dataset_root: Path
    derived_dir: Path
    labels_path: Path
    benchmark_runs_dir: Path
    latest_run_path: Path
    ablation_runs_dir: Path
    latest_ablation_run_path: Path
    alt_feature_rows_path: Path

    @classmethod
    def from_base(
        cls,
        *,
        base_dir: Path,
        dataset_id: str,
        labels_path: Path | None = None,
    ) -> "BenchmarkDatasetLayout":
        dataset_root = base_dir / dataset_id
        derived_dir = dataset_root / "derived"
        effective_labels_path = labels_path if labels_path is not None else (derived_dir / "labels.jsonl")
        return cls(
            dataset_id=dataset_id,
            dataset_root=dataset_root,
            derived_dir=derived_dir,
            labels_path=effective_labels_path,
            benchmark_runs_dir=derived_dir / "benchmark_runs",
            latest_run_path=derived_dir / "latest_benchmark_run.json",
            ablation_runs_dir=derived_dir / "ablation_runs",
            latest_ablation_run_path=derived_dir / "latest_ablation_run.json",
            alt_feature_rows_path=derived_dir / "alt_feature_store" / ALT_FEATURE_SCHEMA_VERSION / "alt_feature_rows.jsonl",
        )


class StrategyResearchBenchmarkService:
    def __init__(
        self,
        *,
        historical_base_dir: Path,
        research_corpus_base_dir: Path,
        default_dataset_id: str,
        default_corpus_id: str,
        prediction_settings: PredictionSettings,
    ) -> None:
        self.historical_base_dir = historical_base_dir
        self.research_corpus_base_dir = research_corpus_base_dir
        self.default_dataset_id = default_dataset_id
        self.default_corpus_id = default_corpus_id
        self.prediction_settings = prediction_settings

    def build_labels(
        self,
        *,
        dataset_id: str | None = None,
        corpus_id: str | None = None,
        labels_path: Path | None = None,
    ) -> LabelBuildSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        layout = BenchmarkDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=labels_path,
        )
        corpus_root: Path | None = self.research_corpus_base_dir / effective_corpus_id
        if corpus_root is not None and not corpus_root.exists():
            corpus_root = None
        builder = LabelDatasetBuilder(
            dataset_id=effective_dataset_id,
            dataset_root=layout.dataset_root,
            corpus_root=corpus_root,
            labels_path=layout.labels_path,
        )
        _, summary = builder.build()
        return summary

    def run_benchmarks(
        self,
        *,
        dataset_id: str | None = None,
        labels_path: Path | None = None,
        split_mode: str = "holdout",
        train_ratio: float = 0.6,
        validation_ratio: float = 0.2,
        train_days: int = 120,
        validation_days: int = 30,
        test_days: int = 30,
        step_days: int = 30,
        max_folds: int = 0,
        min_confidence: float | None = None,
        min_edge_bps: int | None = None,
    ) -> BenchmarkRunSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = BenchmarkDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=labels_path,
        )
        rows = self._read_labels(layout.labels_path)
        if not rows:
            raise ValueError(f"No labels found at {layout.labels_path}. Run build-labels first.")

        normalized_split_mode = split_mode.strip().lower()
        if normalized_split_mode not in {"holdout", "walk-forward"}:
            raise ValueError("split_mode must be one of: holdout, walk-forward")

        if normalized_split_mode == "holdout":
            folds = [build_holdout_fold(rows, train_ratio=train_ratio, validation_ratio=validation_ratio)]
        else:
            folds = build_walk_forward_folds(
                rows,
                train_days=train_days,
                validation_days=validation_days,
                test_days=test_days,
                step_days=step_days,
                max_folds=max_folds,
            )
        if not folds:
            raise ValueError("No valid temporal folds produced. Check date coverage and split parameters.")

        warnings: list[str] = []
        for fold in folds:
            errors = temporal_leakage_errors(fold)
            if errors:
                raise ValueError("; ".join(errors))
            if not fold.validation.rows:
                warnings.append(f"fold={fold.fold_index} has empty validation split")
            if not fold.test.rows:
                warnings.append(f"fold={fold.fold_index} has empty test split")

        suite = BaselinePredictorSuite(self.prediction_settings)
        effective_min_confidence = (
            min_confidence if min_confidence is not None else self.prediction_settings.min_confidence
        )
        effective_min_edge_bps = min_edge_bps if min_edge_bps is not None else self.prediction_settings.min_edge_bps
        min_edge_probability = effective_min_edge_bps / 10_000.0

        fold_payloads: list[dict[str, Any]] = []
        aggregate_bucket: dict[str, dict[str, list[BenchmarkMetrics]]] = {}
        for fold in folds:
            train_rows = list(fold.train.rows)
            stats = fit_training_stats(train_rows)
            split_payloads: dict[str, dict[str, Any]] = {}
            for split_name, split_rows in {
                "train": list(fold.train.rows),
                "validation": list(fold.validation.rows),
                "test": list(fold.test.rows),
            }.items():
                baseline_metrics: dict[str, BenchmarkMetrics] = {}
                for baseline in BASELINE_NAMES:
                    predictions = []
                    for row in split_rows:
                        row_predictions = suite.predict_all(row, training_stats=stats)
                        predictions.append(row_predictions[baseline])
                    metrics = evaluate_predictions(
                        predictions,
                        min_confidence=effective_min_confidence,
                        min_edge_probability=min_edge_probability,
                    )
                    baseline_metrics[baseline] = metrics
                    aggregate_bucket.setdefault(split_name, {}).setdefault(baseline, []).append(metrics)
                split_payloads[split_name] = {
                    baseline: metric.to_dict()
                    for baseline, metric in baseline_metrics.items()
                }
            fold_payloads.append(
                {
                    "fold_index": fold.fold_index,
                    "ranges": {
                        "train": _range_payload(fold.train.rows),
                        "validation": _range_payload(fold.validation.rows),
                        "test": _range_payload(fold.test.rows),
                    },
                    "splits": split_payloads,
                }
            )

        aggregate: dict[str, dict[str, BenchmarkMetrics]] = {}
        for split_name, baseline_map in aggregate_bucket.items():
            aggregate[split_name] = {}
            for baseline, metrics_list in baseline_map.items():
                aggregate[split_name][baseline] = aggregate_metrics(metrics_list)

        run_id = f"bench-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        layout.benchmark_runs_dir.mkdir(parents=True, exist_ok=True)
        run_path = layout.benchmark_runs_dir / f"{run_id}.json"
        payload = {
            "run_id": run_id,
            "dataset_id": effective_dataset_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "split_mode": normalized_split_mode,
            "parameters": {
                "train_ratio": train_ratio,
                "validation_ratio": validation_ratio,
                "train_days": train_days,
                "validation_days": validation_days,
                "test_days": test_days,
                "step_days": step_days,
                "max_folds": max_folds,
            },
            "thresholds": {
                "min_confidence": effective_min_confidence,
                "min_edge_bps": effective_min_edge_bps,
                "min_edge_probability": min_edge_probability,
            },
            "baselines": list(BASELINE_NAMES),
            "folds": fold_payloads,
            "aggregate": {
                split_name: {
                    baseline: metrics.to_dict()
                    for baseline, metrics in baseline_metrics.items()
                }
                for split_name, baseline_metrics in aggregate.items()
            },
            "warnings": warnings,
        }
        run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        layout.latest_run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        return BenchmarkRunSummary(
            dataset_id=effective_dataset_id,
            run_id=run_id,
            run_path=run_path,
            split_mode=normalized_split_mode,
            min_confidence=float(effective_min_confidence),
            min_edge_bps=int(effective_min_edge_bps),
            folds=len(folds),
            aggregate=aggregate,
            warnings=tuple(warnings),
        )

    def compare_benchmarks(
        self,
        *,
        dataset_id: str | None = None,
        run_a: str | None = None,
        run_b: str | None = None,
        split: str = "test",
    ) -> BenchmarkComparisonSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = BenchmarkDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=None,
        )
        runs = self._resolve_comparison_runs(layout=layout, run_a=run_a, run_b=run_b)
        payload_a = runs[0][1]
        payload_b = runs[1][1]
        run_id_a = runs[0][0]
        run_id_b = runs[1][0]
        split_name = split.strip().lower()

        aggregate_a = _extract_split_aggregate(payload_a, split_name)
        aggregate_b = _extract_split_aggregate(payload_b, split_name)
        baselines = sorted(set(aggregate_a.keys()) | set(aggregate_b.keys()))

        deltas: dict[str, dict[str, float]] = {}
        for baseline in baselines:
            metrics_a = BenchmarkMetrics.from_dict(aggregate_a.get(baseline, {}))
            metrics_b = BenchmarkMetrics.from_dict(aggregate_b.get(baseline, {}))
            deltas[baseline] = {
                "delta_brier_score": round(metrics_b.brier_score - metrics_a.brier_score, 8),
                "delta_log_loss": round(metrics_b.log_loss - metrics_a.log_loss, 8),
                "delta_calibration_error": round(metrics_b.calibration_error - metrics_a.calibration_error, 8),
                "delta_approval_rate": round(metrics_b.approval_rate - metrics_a.approval_rate, 8),
                "delta_edge_capture_ratio": round(metrics_b.edge_capture_ratio - metrics_a.edge_capture_ratio, 8),
                "delta_realized_edge_total": round(metrics_b.realized_edge_total - metrics_a.realized_edge_total, 8),
            }

        return BenchmarkComparisonSummary(
            dataset_id=effective_dataset_id,
            run_a=run_id_a,
            run_b=run_id_b,
            split=split_name,
            baseline_deltas=deltas,
        )

    def run_ablation_study(
        self,
        *,
        dataset_id: str | None = None,
        labels_path: Path | None = None,
        alt_feature_rows_path: Path | None = None,
        split_mode: str = "holdout",
        train_ratio: float = 0.6,
        validation_ratio: float = 0.2,
        train_days: int = 120,
        validation_days: int = 30,
        test_days: int = 30,
        step_days: int = 30,
        max_folds: int = 0,
        min_confidence: float | None = None,
        min_edge_bps: int | None = None,
    ) -> AblationRunSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = BenchmarkDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=labels_path,
        )
        rows = self._read_labels(layout.labels_path)
        if not rows:
            raise ValueError(f"No labels found at {layout.labels_path}. Run build-labels first.")

        effective_alt_path = alt_feature_rows_path if alt_feature_rows_path is not None else layout.alt_feature_rows_path
        if not effective_alt_path.exists():
            raise FileNotFoundError(
                f"Alt feature rows file not found: {effective_alt_path}. "
                "Run build-alt-feature-dataset first or pass --alt-feature-rows-path."
            )
        alt_rows_by_id = load_alt_feature_rows(effective_alt_path)

        normalized_split_mode = split_mode.strip().lower()
        if normalized_split_mode not in {"holdout", "walk-forward"}:
            raise ValueError("split_mode must be one of: holdout, walk-forward")
        if normalized_split_mode == "holdout":
            folds = [build_holdout_fold(rows, train_ratio=train_ratio, validation_ratio=validation_ratio)]
        else:
            folds = build_walk_forward_folds(
                rows,
                train_days=train_days,
                validation_days=validation_days,
                test_days=test_days,
                step_days=step_days,
                max_folds=max_folds,
            )
        if not folds:
            raise ValueError("No valid temporal folds produced. Check date coverage and split parameters.")

        warnings: list[str] = []
        for fold in folds:
            errors = temporal_leakage_errors(fold)
            if errors:
                raise ValueError("; ".join(errors))
            if not fold.validation.rows:
                warnings.append(f"fold={fold.fold_index} has empty validation split")
            if not fold.test.rows:
                warnings.append(f"fold={fold.fold_index} has empty test split")

        effective_min_confidence = (
            min_confidence if min_confidence is not None else self.prediction_settings.min_confidence
        )
        effective_min_edge_bps = min_edge_bps if min_edge_bps is not None else self.prediction_settings.min_edge_bps
        min_edge_probability = effective_min_edge_bps / 10_000.0
        predictor = AblationPredictorSuite()

        fold_payloads: list[dict[str, Any]] = []
        aggregate_bucket: dict[str, dict[str, list[BenchmarkMetrics]]] = {}
        test_metrics_per_variant: dict[str, list[BenchmarkMetrics]] = {variant: [] for variant in ABLATION_VARIANT_NAMES}
        alt_missing_by_variant: dict[str, int] = {variant: 0 for variant in ABLATION_VARIANT_NAMES}

        for fold in folds:
            train_rows = list(fold.train.rows)
            models = {
                variant: predictor.train_model(
                    variant=variant,
                    train_rows=train_rows,
                    alt_rows_by_id=alt_rows_by_id,
                )
                for variant in ABLATION_VARIANT_NAMES
            }
            split_payloads: dict[str, dict[str, Any]] = {}
            for split_name, split_rows in {
                "train": list(fold.train.rows),
                "validation": list(fold.validation.rows),
                "test": list(fold.test.rows),
            }.items():
                variant_metrics: dict[str, BenchmarkMetrics] = {}
                for variant in ABLATION_VARIANT_NAMES:
                    predictions = []
                    for row in split_rows:
                        alt_row = alt_rows_by_id.get(row.row_id)
                        if alt_row is None:
                            alt_row = AltFeatureRow.zero(row.row_id)
                            if uses_alt_features(variant):
                                alt_missing_by_variant[variant] += 1
                        prediction = predictor.predict(
                            variant=variant,
                            row=row,
                            alt_row=alt_row,
                            model=models[variant],
                        )
                        predictions.append(prediction)
                    metrics = evaluate_predictions(
                        predictions,
                        min_confidence=effective_min_confidence,
                        min_edge_probability=min_edge_probability,
                    )
                    variant_metrics[variant] = metrics
                    aggregate_bucket.setdefault(split_name, {}).setdefault(variant, []).append(metrics)
                    if split_name == "test":
                        test_metrics_per_variant[variant].append(metrics)
                split_payloads[split_name] = {
                    variant: metric.to_dict()
                    for variant, metric in variant_metrics.items()
                }

            fold_payloads.append(
                {
                    "fold_index": fold.fold_index,
                    "ranges": {
                        "train": _range_payload(fold.train.rows),
                        "validation": _range_payload(fold.validation.rows),
                        "test": _range_payload(fold.test.rows),
                    },
                    "splits": split_payloads,
                }
            )

        aggregate_metrics_by_split: dict[str, dict[str, BenchmarkMetrics]] = {}
        for split_name, variant_map in aggregate_bucket.items():
            aggregate_metrics_by_split[split_name] = {
                variant: aggregate_metrics(metrics_list)
                for variant, metrics_list in variant_map.items()
            }
        robustness = {
            variant: _walk_forward_robustness(test_metrics_per_variant.get(variant, ()))
            for variant in ABLATION_VARIANT_NAMES
        }
        aggregate_payload = _build_ablation_aggregate_payload(aggregate_metrics_by_split, robustness)

        for variant, missing_count in alt_missing_by_variant.items():
            if missing_count > 0 and uses_alt_features(variant):
                warnings.append(f"variant={variant} used zero-alt fallback for {missing_count} rows")
        if normalized_split_mode == "holdout":
            warnings.append("walk_forward_robustness is less informative with split_mode=holdout")

        run_id = f"ablation-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        layout.ablation_runs_dir.mkdir(parents=True, exist_ok=True)
        run_path = layout.ablation_runs_dir / f"{run_id}.json"
        report_path = layout.ablation_runs_dir / f"{run_id}.md"
        payload = {
            "run_id": run_id,
            "dataset_id": effective_dataset_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "split_mode": normalized_split_mode,
            "parameters": {
                "train_ratio": train_ratio,
                "validation_ratio": validation_ratio,
                "train_days": train_days,
                "validation_days": validation_days,
                "test_days": test_days,
                "step_days": step_days,
                "max_folds": max_folds,
                "alt_feature_rows_path": str(effective_alt_path),
            },
            "thresholds": {
                "min_confidence": effective_min_confidence,
                "min_edge_bps": effective_min_edge_bps,
                "min_edge_probability": min_edge_probability,
            },
            "variants": list(ABLATION_VARIANT_NAMES),
            "folds": fold_payloads,
            "aggregate": aggregate_payload,
            "warnings": warnings,
        }
        run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        report_body = _render_ablation_report(payload=payload, split_name="test")
        report_path.write_text(report_body, encoding="utf-8")
        layout.latest_ablation_run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        return AblationRunSummary(
            dataset_id=effective_dataset_id,
            run_id=run_id,
            run_path=run_path,
            report_path=report_path,
            split_mode=normalized_split_mode,
            min_confidence=float(effective_min_confidence),
            min_edge_bps=int(effective_min_edge_bps),
            folds=len(folds),
            variants=ABLATION_VARIANT_NAMES,
            aggregate=aggregate_payload,
            warnings=tuple(warnings),
        )

    def compare_alt_data_variants(
        self,
        *,
        dataset_id: str | None = None,
        run_id: str | None = None,
        split: str = "test",
        reference_variant: str = "market_only_baseline",
        output_path: Path | None = None,
    ) -> AltDataVariantComparisonSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = BenchmarkDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=None,
        )
        selected_run_id, payload = self._resolve_ablation_run(layout=layout, run_id=run_id)
        split_name = split.strip().lower()
        aggregate = payload.get("aggregate")
        if not isinstance(aggregate, Mapping):
            raise ValueError(f"Invalid ablation payload for run_id={selected_run_id}: missing aggregate")
        split_payload_raw = aggregate.get(split_name)
        if not isinstance(split_payload_raw, Mapping):
            raise ValueError(f"Ablation run {selected_run_id} has no split={split_name}")

        split_payload: dict[str, BenchmarkMetrics] = {}
        for variant, metrics_payload in split_payload_raw.items():
            if not isinstance(metrics_payload, Mapping):
                continue
            split_payload[str(variant)] = BenchmarkMetrics.from_dict(metrics_payload)
        if reference_variant not in split_payload:
            raise ValueError(
                f"reference_variant={reference_variant} not available in ablation run {selected_run_id} split={split_name}"
            )

        reference = split_payload[reference_variant]
        ranking = tuple(sorted(split_payload.keys(), key=lambda variant: split_payload[variant].brier_score))
        deltas: dict[str, dict[str, float]] = {}
        for variant in ranking:
            metrics = split_payload[variant]
            deltas[variant] = {
                "delta_brier_score": round(metrics.brier_score - reference.brier_score, 8),
                "delta_log_loss": round(metrics.log_loss - reference.log_loss, 8),
                "delta_calibration_error": round(metrics.calibration_error - reference.calibration_error, 8),
                "delta_approval_rate": round(metrics.approval_rate - reference.approval_rate, 8),
                "delta_realized_edge_total": round(metrics.realized_edge_total - reference.realized_edge_total, 8),
                "delta_edge_capture_ratio": round(metrics.edge_capture_ratio - reference.edge_capture_ratio, 8),
            }

        report_target = (
            output_path
            if output_path is not None
            else (layout.ablation_runs_dir / f"{selected_run_id}-{split_name}-variant-comparison.md")
        )
        report_body = _render_variant_comparison_report(
            run_id=selected_run_id,
            split_name=split_name,
            reference_variant=reference_variant,
            ranking=ranking,
            split_payload=split_payload,
            deltas=deltas,
        )
        report_target.parent.mkdir(parents=True, exist_ok=True)
        report_target.write_text(report_body, encoding="utf-8")

        return AltDataVariantComparisonSummary(
            dataset_id=effective_dataset_id,
            run_id=selected_run_id,
            split=split_name,
            reference_variant=reference_variant,
            report_path=report_target,
            ranking=ranking,
            variant_deltas=deltas,
        )

    @staticmethod
    def _read_labels(path: Path) -> list[LabelRecord]:
        if not path.exists():
            return []
        rows: list[LabelRecord] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if isinstance(raw, dict):
                    rows.append(LabelRecord.from_dict(raw))
        return rows

    @staticmethod
    def _resolve_comparison_runs(
        *,
        layout: BenchmarkDatasetLayout,
        run_a: str | None,
        run_b: str | None,
    ) -> tuple[tuple[str, Mapping[str, Any]], tuple[str, Mapping[str, Any]]]:
        def _load(run_id: str) -> tuple[str, Mapping[str, Any]]:
            path = layout.benchmark_runs_dir / f"{run_id}.json"
            if not path.exists():
                raise FileNotFoundError(f"Benchmark run not found: {path}")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid benchmark run payload: {path}")
            return run_id, raw

        if run_a and run_b:
            return _load(run_a), _load(run_b)

        if not layout.benchmark_runs_dir.exists():
            raise FileNotFoundError("No benchmark runs available for comparison.")
        run_files = sorted(layout.benchmark_runs_dir.glob("bench-*.json"))
        if len(run_files) < 2:
            raise ValueError("At least two benchmark runs are required for compare-benchmarks.")
        selected_a = run_files[-2]
        selected_b = run_files[-1]

        payload_a = json.loads(selected_a.read_text(encoding="utf-8"))
        payload_b = json.loads(selected_b.read_text(encoding="utf-8"))
        if not isinstance(payload_a, dict) or not isinstance(payload_b, dict):
            raise ValueError("Invalid benchmark run payload(s) for comparison.")
        run_id_a = str(payload_a.get("run_id") or selected_a.stem)
        run_id_b = str(payload_b.get("run_id") or selected_b.stem)
        return (run_id_a, payload_a), (run_id_b, payload_b)

    @staticmethod
    def _resolve_ablation_run(
        *,
        layout: BenchmarkDatasetLayout,
        run_id: str | None,
    ) -> tuple[str, Mapping[str, Any]]:
        def _load(path: Path) -> tuple[str, Mapping[str, Any]]:
            if not path.exists():
                raise FileNotFoundError(f"Ablation run not found: {path}")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Invalid ablation run payload: {path}")
            selected_run_id = str(raw.get("run_id") or path.stem)
            return selected_run_id, raw

        if run_id:
            return _load(layout.ablation_runs_dir / f"{run_id}.json")

        if layout.latest_ablation_run_path.exists():
            return _load(layout.latest_ablation_run_path)

        if not layout.ablation_runs_dir.exists():
            raise FileNotFoundError("No ablation runs available. Run run-ablation-study first.")
        run_files = sorted(layout.ablation_runs_dir.glob("ablation-*.json"))
        if not run_files:
            raise FileNotFoundError("No ablation runs available. Run run-ablation-study first.")
        return _load(run_files[-1])


def _range_payload(rows: tuple[LabelRecord, ...]) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "start": None, "end": None}
    return {
        "count": len(rows),
        "start": min(row.decision_timestamp_utc for row in rows).isoformat(),
        "end": max(row.decision_timestamp_utc for row in rows).isoformat(),
    }


def _extract_split_aggregate(payload: Mapping[str, Any], split_name: str) -> Mapping[str, Any]:
    aggregate = payload.get("aggregate")
    if not isinstance(aggregate, Mapping):
        return {}
    split_payload = aggregate.get(split_name)
    if not isinstance(split_payload, Mapping):
        return {}
    return split_payload


def _walk_forward_robustness(metrics: Sequence[BenchmarkMetrics]) -> float:
    metric_rows = tuple(metrics)
    if not metric_rows:
        return 0.0
    if len(metric_rows) == 1:
        return 1.0
    brier_std = _stddev([row.brier_score for row in metric_rows])
    log_loss_std = _stddev([row.log_loss for row in metric_rows])
    approval_std = _stddev([row.approval_rate for row in metric_rows])
    edge_capture_std = _stddev([row.edge_capture_ratio for row in metric_rows])
    penalty = brier_std + log_loss_std + (0.5 * approval_std) + (0.5 * edge_capture_std)
    return round(1.0 / (1.0 + max(penalty, 0.0)), 8)


def _stddev(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance ** 0.5


def _build_ablation_aggregate_payload(
    aggregate: Mapping[str, Mapping[str, BenchmarkMetrics]],
    robustness: Mapping[str, float],
) -> dict[str, dict[str, dict[str, float | int]]]:
    payload: dict[str, dict[str, dict[str, float | int]]] = {}
    for split_name, variant_map in aggregate.items():
        payload[split_name] = {}
        market_only = variant_map.get("market_only_baseline")
        market_only_approval = market_only.approval_rate if market_only is not None else 0.0
        for variant in ABLATION_VARIANT_NAMES:
            metrics = variant_map.get(variant)
            if metrics is None:
                metrics = BenchmarkMetrics(
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
            row_payload: dict[str, float | int] = dict(metrics.to_dict())
            row_payload["approval_rate_delta_vs_market_only"] = round(
                metrics.approval_rate - market_only_approval,
                8,
            )
            row_payload["realized_vs_expected_edge"] = round(metrics.edge_capture_ratio, 8)
            row_payload["walk_forward_robustness"] = round(robustness.get(variant, 0.0), 8)
            payload[split_name][variant] = row_payload
    return payload


def _render_ablation_report(*, payload: Mapping[str, Any], split_name: str) -> str:
    run_id = str(payload.get("run_id") or "")
    dataset_id = str(payload.get("dataset_id") or "")
    aggregate = payload.get("aggregate")
    split_payload = aggregate.get(split_name) if isinstance(aggregate, Mapping) else None
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(split_payload, Mapping):
        for variant, metrics in split_payload.items():
            if isinstance(metrics, Mapping):
                rows.append((str(variant), metrics))
    rows.sort(key=lambda item: float(item[1].get("brier_score", 9e9)))

    lines = [
        "# Alt-Data Ablation Study Report",
        "",
        f"- run_id: `{run_id}`",
        f"- dataset_id: `{dataset_id}`",
        f"- split: `{split_name}`",
        "",
        "| Variant | Brier | Log loss | Cal error | Approval rate | Approval delta vs market | Realized vs expected edge | Walk-forward robustness |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, metrics in rows:
        lines.append(
            "| "
            f"{variant} | "
            f"{float(metrics.get('brier_score', 0.0)):.6f} | "
            f"{float(metrics.get('log_loss', 0.0)):.6f} | "
            f"{float(metrics.get('calibration_error', 0.0)):.6f} | "
            f"{float(metrics.get('approval_rate', 0.0)):.4f} | "
            f"{float(metrics.get('approval_rate_delta_vs_market_only', 0.0)):+.4f} | "
            f"{float(metrics.get('realized_vs_expected_edge', 0.0)):.6f} | "
            f"{float(metrics.get('walk_forward_robustness', 0.0)):.6f} |"
        )
    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.append("")
        lines.append("## Warnings")
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def _render_variant_comparison_report(
    *,
    run_id: str,
    split_name: str,
    reference_variant: str,
    ranking: Sequence[str],
    split_payload: Mapping[str, BenchmarkMetrics],
    deltas: Mapping[str, Mapping[str, float]],
) -> str:
    lines = [
        "# Alt-Data Variant Comparison",
        "",
        f"- run_id: `{run_id}`",
        f"- split: `{split_name}`",
        f"- reference_variant: `{reference_variant}`",
        "",
        "| Variant | Brier | Log loss | Cal error | Approval rate | Edge capture | Delta Brier | Delta Log loss | Delta Approval |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in ranking:
        metrics = split_payload[variant]
        delta = deltas.get(variant, {})
        lines.append(
            "| "
            f"{variant} | "
            f"{metrics.brier_score:.6f} | "
            f"{metrics.log_loss:.6f} | "
            f"{metrics.calibration_error:.6f} | "
            f"{metrics.approval_rate:.4f} | "
            f"{metrics.edge_capture_ratio:.6f} | "
            f"{float(delta.get('delta_brier_score', 0.0)):+.6f} | "
            f"{float(delta.get('delta_log_loss', 0.0)):+.6f} | "
            f"{float(delta.get('delta_approval_rate', 0.0)):+.4f} |"
        )
    lines.append("")
    return "\n".join(lines)
