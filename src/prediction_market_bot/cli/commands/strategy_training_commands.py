from __future__ import annotations

import json
import logging
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.strategy_research.training import StrategyTrainingLabService

logger = logging.getLogger(__name__)


def _build_training_service(config_path: Path, agents_config_path: Path) -> StrategyTrainingLabService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return StrategyTrainingLabService(
        historical_base_dir=Path(sr.base_dir),
        default_dataset_id=sr.default_dataset_id,
        model_card_template_path=Path("docs/MODEL_CARD_TEMPLATE.md"),
    )


def train_baseline_models_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    feature_rows_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    window_days: int,
    include_xgboost: bool,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.train_baseline_models(
        dataset_id=dataset_id,
        feature_rows_path=feature_rows_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        window_days=window_days,
        include_xgboost=include_xgboost,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Offline training completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"best_model={summary.best_model} split_mode={summary.split_mode}"
        )
        print(f"Run path: {summary.run_path}")
        for model_name, result in summary.models.items():
            test_metrics = result.metrics_by_split.get("test")
            if result.status != "trained":
                print(f"- {model_name}: status={result.status} reason={result.reason}")
                continue
            if test_metrics is None:
                print(f"- {model_name}: status=trained test_metrics=missing")
                continue
            print(
                f"- {model_name}: brier={test_metrics.brier_score:.6f} "
                f"log_loss={test_metrics.log_loss:.6f} cal={test_metrics.calibration_error:.6f} "
                f"accuracy={test_metrics.accuracy:.4f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_train_baseline_models_completed",
        extra={
            "event": "strategy_research_train_baseline_models_completed",
            **payload,
        },
    )
    return 0


def calibrate_model_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    model_name: str | None,
    method: str,
    fit_split: str,
    eval_split: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.calibrate_model(
        dataset_id=dataset_id,
        run_id=run_id,
        model_name=model_name,
        method=method,
        fit_split=fit_split,
        eval_split=eval_split,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Calibration completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"train_run_id={summary.train_run_id} model={summary.model_name} method={summary.method}"
        )
        print(
            "Eval split metrics: "
            f"raw_brier={summary.raw_metrics.brier_score:.6f} "
            f"calibrated_brier={summary.calibrated_metrics.brier_score:.6f} "
            f"raw_log_loss={summary.raw_metrics.log_loss:.6f} "
            f"calibrated_log_loss={summary.calibrated_metrics.log_loss:.6f}"
        )
        print(f"Calibration artifact: {summary.calibration_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_calibrate_model_completed",
        extra={
            "event": "strategy_research_calibrate_model_completed",
            **payload,
        },
    )
    return 0


def compare_models_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    split: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.compare_models(
        dataset_id=dataset_id,
        run_id=run_id,
        split=split,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model comparison completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} split={summary.split}"
        )
        for rank, (model_name, metrics) in enumerate(summary.ranking, start=1):
            deltas = summary.deltas.get(model_name, {})
            print(
                f"{rank}. {model_name}: "
                f"brier={metrics.brier_score:.6f} log_loss={metrics.log_loss:.6f} "
                f"cal={metrics.calibration_error:.6f} acc={metrics.accuracy:.4f} "
                f"delta_brier={deltas.get('delta_brier_score_vs_best', 0.0):+.6f}"
            )
    logger.info(
        "strategy_research_compare_models_completed",
        extra={
            "event": "strategy_research_compare_models_completed",
            **payload,
        },
    )
    return 0


def generate_model_card_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    model_name: str | None,
    calibration_run_id: str | None,
    output_path: Path | None,
    owner: str,
    decision: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.generate_model_card(
        dataset_id=dataset_id,
        run_id=run_id,
        model_name=model_name,
        calibration_run_id=calibration_run_id,
        output_path=output_path,
        owner=owner,
        decision=decision,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model card generated "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"model={summary.model_name} decision={summary.decision}"
        )
        print(f"Output path: {summary.output_path}")
    logger.info(
        "strategy_research_model_card_generated",
        extra={
            "event": "strategy_research_model_card_generated",
            **payload,
        },
    )
    return 0
