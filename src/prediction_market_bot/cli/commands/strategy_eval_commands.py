from __future__ import annotations

import json
import logging
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.strategy_research.benchmarking import StrategyResearchBenchmarkService
from prediction_market_bot.strategy_research.simulator import WalkForwardStrategyService

logger = logging.getLogger(__name__)


def _build_benchmark_service(config_path: Path, agents_config_path: Path) -> StrategyResearchBenchmarkService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return StrategyResearchBenchmarkService(
        historical_base_dir=Path(sr.base_dir),
        research_corpus_base_dir=Path(sr.research_corpus_base_dir),
        default_dataset_id=sr.default_dataset_id,
        default_corpus_id=sr.research_corpus_default_corpus_id,
        prediction_settings=settings.prediction,
    )


def _build_walk_forward_service(config_path: Path, agents_config_path: Path) -> WalkForwardStrategyService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return WalkForwardStrategyService(
        historical_base_dir=Path(sr.base_dir),
        default_dataset_id=sr.default_dataset_id,
        prediction_settings=settings.prediction,
    )


def build_labels_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    corpus_id: str | None,
    labels_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.build_labels(
        dataset_id=dataset_id,
        corpus_id=corpus_id,
        labels_path=labels_path,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Labels build completed "
            f"dataset_id={summary.dataset_id} labels_written={summary.labels_written} "
            f"skipped_missing_timestamp={summary.skipped_missing_timestamp} "
            f"skipped_unresolved_or_ambiguous={summary.skipped_unresolved_or_ambiguous}"
        )
        print(f"Labels path: {summary.labels_path}")
        print(f"Manifest: {summary.labels_manifest_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_labels_built",
        extra={
            "event": "strategy_research_labels_built",
            **payload,
        },
    )
    return 0


def run_benchmarks_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.run_benchmarks(
        dataset_id=dataset_id,
        labels_path=labels_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Benchmarks completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"split_mode={summary.split_mode} folds={summary.folds}"
        )
        print(f"Run path: {summary.run_path}")
        print("Aggregate test split:")
        test_metrics = summary.aggregate.get("test", {})
        for baseline_name, metrics in test_metrics.items():
            print(
                f"- {baseline_name}: brier={metrics.brier_score:.6f} "
                f"log_loss={metrics.log_loss:.6f} cal={metrics.calibration_error:.6f} "
                f"approval_rate={metrics.approval_rate:.4f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_benchmarks_completed",
        extra={
            "event": "strategy_research_benchmarks_completed",
            **payload,
        },
    )
    return 0


def compare_benchmarks_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_a: str | None,
    run_b: str | None,
    split: str,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    comparison = service.compare_benchmarks(
        dataset_id=dataset_id,
        run_a=run_a,
        run_b=run_b,
        split=split,
    )
    payload = comparison.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Benchmark comparison completed "
            f"dataset_id={comparison.dataset_id} run_a={comparison.run_a} run_b={comparison.run_b} split={comparison.split}"
        )
        for baseline_name, deltas in comparison.baseline_deltas.items():
            print(
                f"- {baseline_name}: "
                f"delta_brier={deltas.get('delta_brier_score', 0.0):+.6f} "
                f"delta_log_loss={deltas.get('delta_log_loss', 0.0):+.6f} "
                f"delta_cal={deltas.get('delta_calibration_error', 0.0):+.6f} "
                f"delta_approval={deltas.get('delta_approval_rate', 0.0):+.4f}"
            )
    logger.info(
        "strategy_research_benchmarks_compared",
        extra={
            "event": "strategy_research_benchmarks_compared",
            **payload,
        },
    )
    return 0


def run_walk_forward_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    baseline_name: str,
    eval_split: str,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    initial_bankroll_usd: float,
    base_position_pct: float,
    max_position_pct: float,
    min_stake_usd: float,
    fee_bps: int,
    slippage_bps: int,
    as_json: bool,
) -> int:
    service = _build_walk_forward_service(config_path, agents_config_path)
    summary = service.run_walk_forward(
        dataset_id=dataset_id,
        labels_path=labels_path,
        baseline_name=baseline_name,
        eval_split=eval_split,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
        initial_bankroll_usd=initial_bankroll_usd,
        base_position_pct=base_position_pct,
        max_position_pct=max_position_pct,
        min_stake_usd=min_stake_usd,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        metrics = summary.aggregate_metrics
        print(
            "Walk-forward strategy simulation completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} folds={len(summary.folds)} "
            f"baseline={summary.baseline_name} eval_split={summary.eval_split}"
        )
        print(f"Run path: {summary.run_path}")
        print(
            "Aggregate metrics: "
            f"roi={metrics.roi:.6f} pnl_usd={metrics.pnl_usd:.6f} "
            f"max_drawdown_pct={metrics.max_drawdown_pct:.6f} turnover={metrics.turnover:.6f} "
            f"approval_rate={metrics.approval_rate:.6f} "
            f"expected_edge_total={metrics.expected_edge_total:.6f} "
            f"realized_edge_total={metrics.realized_edge_total:.6f} "
            f"edge_capture_ratio={metrics.edge_capture_ratio:.6f}"
        )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_walk_forward_completed",
        extra={
            "event": "strategy_research_walk_forward_completed",
            **payload,
        },
    )
    return 0


def generate_strategy_report_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    output_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_walk_forward_service(config_path, agents_config_path)
    summary = service.generate_strategy_report(
        dataset_id=dataset_id,
        run_id=run_id,
        output_path=output_path,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Strategy report generated "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} report_path={summary.report_path}"
        )
    logger.info(
        "strategy_research_walk_forward_report_generated",
        extra={
            "event": "strategy_research_walk_forward_report_generated",
            **payload,
        },
    )
    return 0


def run_ablation_study_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    alt_feature_rows_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.run_ablation_study(
        dataset_id=dataset_id,
        labels_path=labels_path,
        alt_feature_rows_path=alt_feature_rows_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Ablation study completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"split_mode={summary.split_mode} folds={summary.folds}"
        )
        print(f"Run path: {summary.run_path}")
        print(f"Report path: {summary.report_path}")
        test_metrics = summary.aggregate.get("test", {})
        print("Aggregate test split:")
        for variant_name, metrics in test_metrics.items():
            print(
                f"- {variant_name}: "
                f"brier={float(metrics.get('brier_score', 0.0)):.6f} "
                f"log_loss={float(metrics.get('log_loss', 0.0)):.6f} "
                f"cal={float(metrics.get('calibration_error', 0.0)):.6f} "
                f"approval_rate={float(metrics.get('approval_rate', 0.0)):.4f} "
                f"approval_delta_vs_market={float(metrics.get('approval_rate_delta_vs_market_only', 0.0)):+.4f} "
                f"edge_ratio={float(metrics.get('realized_vs_expected_edge', 0.0)):.6f} "
                f"robustness={float(metrics.get('walk_forward_robustness', 0.0)):.6f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_ablation_study_completed",
        extra={
            "event": "strategy_research_ablation_study_completed",
            **payload,
        },
    )
    return 0
