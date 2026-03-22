from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.services.history.history_queries import list_run_ids
from prediction_market_bot.services.history.shadow_scoring_service import ShadowScoringReport, build_shadow_scoring_report
from prediction_market_bot.services.operator_control import OperatorControlState


@dataclass(slots=True, frozen=True)
class PromotionCheck:
    name: str
    passed: bool
    detail: str
    value: float | None = None
    threshold: float | None = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "value": self.value,
            "threshold": self.threshold,
            "severity": self.severity,
        }


@dataclass(slots=True, frozen=True)
class PromotionEvaluation:
    dataset_id: str
    created_at_utc: str
    benchmark_run_id: str
    training_run_id: str
    walk_forward_run_id: str
    shadow_run_id: str
    model_version: str
    checks: tuple[PromotionCheck, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks if check.severity == "error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "created_at_utc": self.created_at_utc,
            "passed": self.passed,
            "benchmark_run_id": self.benchmark_run_id,
            "training_run_id": self.training_run_id,
            "walk_forward_run_id": self.walk_forward_run_id,
            "shadow_run_id": self.shadow_run_id,
            "model_version": self.model_version,
            "checks": [check.to_dict() for check in self.checks],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class DriftSignal:
    name: str
    status: str
    detail: str
    current_value: float | None = None
    reference_value: float | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "current_value": self.current_value,
            "reference_value": self.reference_value,
            "threshold": self.threshold,
        }


@dataclass(slots=True, frozen=True)
class DriftReport:
    current_run_id: str
    reference_run_ids: tuple[str, ...]
    created_at_utc: str
    overall_status: str
    signals: tuple[DriftSignal, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_run_id": self.current_run_id,
            "reference_run_ids": list(self.reference_run_ids),
            "created_at_utc": self.created_at_utc,
            "overall_status": self.overall_status,
            "signals": [signal.to_dict() for signal in self.signals],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class RuntimeModelGateDecision:
    requested_engine: str
    effective_engine: str
    allowed: bool
    gate_required: bool
    reason: str
    model_version: str
    promoted_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_engine": self.requested_engine,
            "effective_engine": self.effective_engine,
            "allowed": self.allowed,
            "gate_required": self.gate_required,
            "reason": self.reason,
            "model_version": self.model_version,
            "promoted_version": self.promoted_version,
        }


def gate_required_for_runtime_mode(settings: AppSettings) -> bool:
    if not settings.model_promotion.enabled:
        return False
    if not settings.model_promotion.require_explicit_approval_in_live_modes:
        return False
    allowed_modes = {item.strip().upper() for item in settings.model_promotion.required_runtime_modes if item.strip()}
    if not allowed_modes:
        return False
    return settings.runtime.mode.value in allowed_modes


def promotion_activation_allowed_for_runtime_mode(settings: AppSettings) -> bool:
    activation_modes = {
        item.strip().upper()
        for item in settings.model_promotion.promoted_runtime_modes
        if item.strip()
    }
    if not activation_modes:
        return False
    return settings.runtime.mode.value in activation_modes


def read_model_artifact_version(model_artifact_path: str) -> str:
    artifact_path = Path(model_artifact_path.strip())
    if not artifact_path.exists():
        return ""
    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(raw, Mapping):
        return ""
    return str(raw.get("model_version") or "").strip()


def resolve_runtime_model_gate_decision(
    *,
    settings: AppSettings,
    state: OperatorControlState,
) -> RuntimeModelGateDecision:
    requested_engine = settings.prediction.engine.strip().lower()
    model_version = read_model_artifact_version(settings.prediction.model_artifact_path)
    promoted_version = state.model_v2_promoted_model_version.strip()

    if requested_engine not in {"model_v2", "artifact_model", "model", "model_v2_alt_promoted", "model_v2_alt"}:
        return RuntimeModelGateDecision(
            requested_engine=requested_engine,
            effective_engine=requested_engine,
            allowed=True,
            gate_required=False,
            reason="prediction_engine_not_model_v2",
            model_version=model_version,
            promoted_version=promoted_version,
        )

    if state.model_v2_rollback_active:
        return RuntimeModelGateDecision(
            requested_engine=requested_engine,
            effective_engine="heuristic",
            allowed=False,
            gate_required=gate_required_for_runtime_mode(settings),
            reason="model_v2_rollback_active",
            model_version=model_version,
            promoted_version=promoted_version,
        )

    gate_required = gate_required_for_runtime_mode(settings)
    activation_allowed = promotion_activation_allowed_for_runtime_mode(settings)
    blocked_reason = ""
    if gate_required and not activation_allowed:
        blocked_reason = "model_v2_not_enabled_for_runtime_mode"
    elif gate_required and not state.model_v2_promoted:
        blocked_reason = "model_v2_not_promoted"
    elif gate_required and promoted_version and model_version and promoted_version != model_version:
        blocked_reason = "model_version_mismatch_with_promoted_decision"

    if not blocked_reason:
        return RuntimeModelGateDecision(
            requested_engine=requested_engine,
            effective_engine=requested_engine,
            allowed=True,
            gate_required=gate_required,
            reason="model_v2_allowed",
            model_version=model_version,
            promoted_version=promoted_version,
        )

    if settings.prediction.fallback_to_heuristic:
        return RuntimeModelGateDecision(
            requested_engine=requested_engine,
            effective_engine="heuristic",
            allowed=False,
            gate_required=gate_required,
            reason=blocked_reason,
            model_version=model_version,
            promoted_version=promoted_version,
        )

    return RuntimeModelGateDecision(
        requested_engine=requested_engine,
        effective_engine=requested_engine,
        allowed=False,
        gate_required=gate_required,
        reason=blocked_reason,
        model_version=model_version,
        promoted_version=promoted_version,
    )

def evaluate_model_promotion(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    dataset_id: str | None = None,
    benchmark_run_id: str | None = None,
    training_run_id: str | None = None,
    walk_forward_run_id: str | None = None,
    shadow_run_id: str | None = None,
) -> PromotionEvaluation:
    effective_dataset_id = (dataset_id or settings.strategy_research.default_dataset_id).strip()
    dataset_root = Path(settings.strategy_research.base_dir) / effective_dataset_id
    derived_root = dataset_root / "derived"

    benchmark_payload = _load_benchmark_payload(derived_root=derived_root, run_id=benchmark_run_id)
    training_payload = _load_training_payload(derived_root=derived_root, run_id=training_run_id)
    walk_forward_payload = _load_walk_forward_payload(derived_root=derived_root, run_id=walk_forward_run_id)
    calibration_payload = _load_calibration_payload(derived_root=derived_root)

    resolved_shadow_run_id = (shadow_run_id or _resolve_latest_shadow_run_id(persistence)).strip()
    shadow_report = build_shadow_scoring_report(persistence, resolved_shadow_run_id) if resolved_shadow_run_id else None

    model_artifact = _load_model_artifact(settings.prediction.model_artifact_path)
    model_version = str(model_artifact.get("model_version") or "").strip()
    model_schema_version = str(model_artifact.get("feature_schema_version") or "").strip()
    required_features = tuple(
        str(item).strip()
        for item in model_artifact.get("required_features", [])
        if isinstance(item, (str, int, float)) and str(item).strip()
    )

    checks: list[PromotionCheck] = []
    warnings: list[str] = []

    benchmark_check = _check_benchmark_outperformance(
        benchmark_payload=benchmark_payload,
        training_payload=training_payload,
        min_delta_brier=settings.model_promotion.benchmark_min_delta_brier,
        min_delta_log_loss=settings.model_promotion.benchmark_min_delta_log_loss,
    )
    checks.append(benchmark_check)

    calibration_check, calibration_warning = _check_calibration_quality(
        training_payload=training_payload,
        calibration_payload=calibration_payload,
        max_calibration_error=settings.model_promotion.max_calibration_error,
        max_brier_increase=settings.model_promotion.max_calibration_brier_increase,
    )
    checks.append(calibration_check)
    if calibration_warning:
        warnings.append(calibration_warning)

    approval_rate_check = _check_approval_rate_sanity(
        shadow_report=shadow_report,
        min_rate=settings.model_promotion.approval_rate_min,
        max_rate=settings.model_promotion.approval_rate_max,
    )
    checks.append(approval_rate_check)

    realized_edge_check = _check_realized_edge_consistency(
        walk_forward_payload=walk_forward_payload,
        min_edge_capture_ratio=settings.model_promotion.min_edge_capture_ratio,
        min_realized_vs_expected_ratio=settings.model_promotion.min_realized_vs_expected_edge_ratio,
    )
    checks.append(realized_edge_check)

    feature_check = _check_feature_schema_compatibility(
        derived_root=derived_root,
        required_features=required_features,
        artifact_schema_version=model_schema_version,
        expected_schema_version=settings.prediction.model_expected_feature_schema_version,
        shadow_report=shadow_report,
        max_parity_warning_rate=settings.model_promotion.max_shadow_parity_warning_rate,
    )
    checks.append(feature_check)

    return PromotionEvaluation(
        dataset_id=effective_dataset_id,
        created_at_utc=datetime.now(UTC).isoformat(),
        benchmark_run_id=str(benchmark_payload.get("run_id") or "").strip(),
        training_run_id=str(training_payload.get("run_id") or "").strip(),
        walk_forward_run_id=str(walk_forward_payload.get("run_id") or "").strip(),
        shadow_run_id=resolved_shadow_run_id,
        model_version=model_version,
        checks=tuple(checks),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def build_model_drift_report(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    run_id: str | None = None,
    reference_runs: int | None = None,
) -> DriftReport:
    resolved_run_id = (run_id or _resolve_latest_run_id(persistence)).strip()
    if not resolved_run_id:
        return DriftReport(
            current_run_id="",
            reference_run_ids=(),
            created_at_utc=datetime.now(UTC).isoformat(),
            overall_status="insufficient_data",
            signals=(
                DriftSignal(
                    name="runtime_drift",
                    status="insufficient_data",
                    detail="no_runs_available_for_drift_monitoring",
                ),
            ),
            warnings=("no_runs_available_for_drift_monitoring",),
        )

    ref_target = reference_runs if reference_runs is not None else settings.model_promotion.drift_reference_runs
    recent_runs = list_run_ids(persistence, limit_runs=max(int(ref_target), 1) + 1)
    ordered = [item for item in recent_runs if item.strip()]
    if resolved_run_id not in ordered:
        ordered.append(resolved_run_id)
    ordered = sorted(dict.fromkeys(ordered))
    if resolved_run_id in ordered:
        ordered.remove(resolved_run_id)
    reference_run_ids = tuple(ordered[-max(int(ref_target), 1) :])

    signals: list[DriftSignal] = []
    warnings: list[str] = []

    feature_signal = _feature_distribution_drift_signal(
        persistence=persistence,
        run_id=resolved_run_id,
        reference_run_ids=reference_run_ids,
        threshold=settings.model_promotion.feature_shift_warn_threshold,
    )
    signals.append(feature_signal)
    if feature_signal.status == "insufficient_data":
        warnings.append(feature_signal.detail)

    market_signal = _market_regime_drift_signal(
        persistence=persistence,
        run_id=resolved_run_id,
        reference_run_ids=reference_run_ids,
        threshold=settings.model_promotion.market_regime_warn_threshold,
    )
    signals.append(market_signal)
    if market_signal.status == "insufficient_data":
        warnings.append(market_signal.detail)

    research_signal = _research_coverage_drift_signal(
        persistence=persistence,
        run_id=resolved_run_id,
        reference_run_ids=reference_run_ids,
        threshold=settings.model_promotion.research_coverage_warn_threshold,
    )
    signals.append(research_signal)
    if research_signal.status == "insufficient_data":
        warnings.append(research_signal.detail)

    confidence_signal = _confidence_collapse_signal(
        persistence=persistence,
        run_id=resolved_run_id,
        reference_run_ids=reference_run_ids,
        threshold=settings.model_promotion.confidence_collapse_warn_threshold,
        confidence_floor=settings.prediction.min_confidence,
    )
    signals.append(confidence_signal)
    if confidence_signal.status == "insufficient_data":
        warnings.append(confidence_signal.detail)

    overall_status = _worst_status([signal.status for signal in signals])
    return DriftReport(
        current_run_id=resolved_run_id,
        reference_run_ids=reference_run_ids,
        created_at_utc=datetime.now(UTC).isoformat(),
        overall_status=overall_status,
        signals=tuple(signals),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _check_benchmark_outperformance(
    *,
    benchmark_payload: Mapping[str, Any],
    training_payload: Mapping[str, Any],
    min_delta_brier: float,
    min_delta_log_loss: float,
) -> PromotionCheck:
    aggregate = benchmark_payload.get("aggregate")
    test_metrics = _as_mapping(_as_mapping(aggregate).get("test"))
    market = _as_mapping(test_metrics.get("market_implied"))
    heuristic = _as_mapping(test_metrics.get("heuristic_prediction_agent"))

    models = _as_mapping(training_payload.get("models"))
    best_model = str(training_payload.get("best_model") or "").strip()
    best_metrics = _as_mapping(_as_mapping(models.get(best_model)).get("metrics_by_split")).get("test")
    best_test = _as_mapping(best_metrics)

    model_brier = _as_float(best_test.get("brier_score"))
    model_log_loss = _as_float(best_test.get("log_loss"))
    market_brier = _as_float(market.get("brier_score"))
    market_log_loss = _as_float(market.get("log_loss"))
    heuristic_brier = _as_float(heuristic.get("brier_score"))
    heuristic_log_loss = _as_float(heuristic.get("log_loss"))

    if not best_model or model_brier is None or model_log_loss is None:
        return PromotionCheck(name="benchmark_outperformance", passed=False, detail="missing_training_test_metrics_or_best_model")
    if market_brier is None or market_log_loss is None or heuristic_brier is None or heuristic_log_loss is None:
        return PromotionCheck(
            name="benchmark_outperformance",
            passed=False,
            detail="missing_benchmark_test_metrics_for_market_implied_or_heuristic",
        )

    delta_market_brier = market_brier - model_brier
    delta_market_log_loss = market_log_loss - model_log_loss
    delta_heuristic_brier = heuristic_brier - model_brier
    delta_heuristic_log_loss = heuristic_log_loss - model_log_loss
    passed = (
        delta_market_brier >= min_delta_brier
        and delta_market_log_loss >= min_delta_log_loss
        and delta_heuristic_brier >= 0.0
        and delta_heuristic_log_loss >= 0.0
    )
    detail = (
        f"best_model={best_model} "
        f"delta_market_brier={delta_market_brier:.6f} delta_market_log_loss={delta_market_log_loss:.6f} "
        f"delta_heuristic_brier={delta_heuristic_brier:.6f} delta_heuristic_log_loss={delta_heuristic_log_loss:.6f}"
    )
    return PromotionCheck(
        name="benchmark_outperformance",
        passed=passed,
        detail=detail,
        value=delta_market_brier,
        threshold=min_delta_brier,
    )

def _check_calibration_quality(
    *,
    training_payload: Mapping[str, Any],
    calibration_payload: Mapping[str, Any],
    max_calibration_error: float,
    max_brier_increase: float,
) -> tuple[PromotionCheck, str]:
    models = _as_mapping(training_payload.get("models"))
    best_model = str(training_payload.get("best_model") or "").strip()
    best = _as_mapping(models.get(best_model))
    best_test = _as_mapping(_as_mapping(best.get("metrics_by_split")).get("test"))
    base_cal_error = _as_float(best_test.get("calibration_error"))
    if not best_model or base_cal_error is None:
        return (
            PromotionCheck(
                name="calibration_quality",
                passed=False,
                detail="missing_training_calibration_metrics",
                threshold=max_calibration_error,
            ),
            "",
        )

    cal_model_name = str(calibration_payload.get("model_name") or "").strip()
    cal_train_run_id = str(calibration_payload.get("train_run_id") or "").strip()
    train_run_id = str(training_payload.get("run_id") or "").strip()
    calibrated_metrics = _as_mapping(calibration_payload.get("calibrated_metrics"))
    raw_metrics = _as_mapping(calibration_payload.get("raw_metrics"))

    warning = ""
    if cal_model_name != best_model or cal_train_run_id != train_run_id:
        warning = "calibration_run_does_not_match_best_model_or_training_run"

    calibrated_error = _as_float(calibrated_metrics.get("calibration_error"))
    calibrated_brier = _as_float(calibrated_metrics.get("brier_score"))
    raw_brier = _as_float(raw_metrics.get("brier_score"))

    if calibrated_error is None or calibrated_brier is None or raw_brier is None:
        return (
            PromotionCheck(
                name="calibration_quality",
                passed=False,
                detail="missing_calibration_run_metrics",
                threshold=max_calibration_error,
            ),
            warning,
        )

    brier_increase = calibrated_brier - raw_brier
    passed = calibrated_error <= max_calibration_error and brier_increase <= max_brier_increase
    detail = (
        f"calibration_error={calibrated_error:.6f} max={max_calibration_error:.6f} "
        f"brier_increase={brier_increase:.6f} max_increase={max_brier_increase:.6f}"
    )
    return (
        PromotionCheck(
            name="calibration_quality",
            passed=passed,
            detail=detail,
            value=calibrated_error,
            threshold=max_calibration_error,
        ),
        warning,
    )


def _check_approval_rate_sanity(
    *,
    shadow_report: ShadowScoringReport | None,
    min_rate: float,
    max_rate: float,
) -> PromotionCheck:
    if shadow_report is None:
        return PromotionCheck(
            name="approval_rate_sanity",
            passed=False,
            detail="missing_shadow_scoring_evidence",
            threshold=min_rate,
        )
    model_rate = _as_float(shadow_report.approval_rate.get("model_v2_rate"))
    if model_rate is None:
        return PromotionCheck(
            name="approval_rate_sanity",
            passed=False,
            detail="shadow_report_missing_model_v2_approval_rate",
            threshold=min_rate,
        )
    passed = min_rate <= model_rate <= max_rate
    return PromotionCheck(
        name="approval_rate_sanity",
        passed=passed,
        detail=(
            f"model_v2_rate={model_rate:.6f} expected_range=[{min_rate:.6f},{max_rate:.6f}] "
            f"sample={int(shadow_report.approval_rate.get('sample_size') or 0)}"
        ),
        value=model_rate,
        threshold=min_rate,
    )


def _check_realized_edge_consistency(
    *,
    walk_forward_payload: Mapping[str, Any],
    min_edge_capture_ratio: float,
    min_realized_vs_expected_ratio: float,
) -> PromotionCheck:
    aggregate = _as_mapping(walk_forward_payload.get("aggregate_metrics"))
    expected_edge_total = _as_float(aggregate.get("expected_edge_total"))
    realized_edge_total = _as_float(aggregate.get("realized_edge_total"))
    edge_capture_ratio = _as_float(aggregate.get("edge_capture_ratio"))
    if expected_edge_total is None or realized_edge_total is None or edge_capture_ratio is None:
        return PromotionCheck(
            name="realized_edge_consistency",
            passed=False,
            detail="missing_walk_forward_edge_metrics",
            threshold=min_edge_capture_ratio,
        )
    if expected_edge_total <= 0.0:
        return PromotionCheck(
            name="realized_edge_consistency",
            passed=False,
            detail=f"expected_edge_total_not_positive value={expected_edge_total:.6f}",
            value=expected_edge_total,
            threshold=0.0,
        )
    realized_vs_expected = realized_edge_total / expected_edge_total
    passed = edge_capture_ratio >= min_edge_capture_ratio and realized_vs_expected >= min_realized_vs_expected_ratio
    return PromotionCheck(
        name="realized_edge_consistency",
        passed=passed,
        detail=(
            f"edge_capture_ratio={edge_capture_ratio:.6f} min={min_edge_capture_ratio:.6f} "
            f"realized_vs_expected={realized_vs_expected:.6f} min={min_realized_vs_expected_ratio:.6f}"
        ),
        value=edge_capture_ratio,
        threshold=min_edge_capture_ratio,
    )


def _check_feature_schema_compatibility(
    *,
    derived_root: Path,
    required_features: Sequence[str],
    artifact_schema_version: str,
    expected_schema_version: str,
    shadow_report: ShadowScoringReport | None,
    max_parity_warning_rate: float,
) -> PromotionCheck:
    schema_path = derived_root / "feature_store" / "v1" / "feature_schema.json"
    if not schema_path.exists():
        return PromotionCheck(
            name="feature_schema_compatibility",
            passed=False,
            detail=f"feature_schema_missing path={schema_path}",
            threshold=max_parity_warning_rate,
        )
    schema_payload = _load_json_mapping(schema_path)
    columns = _as_sequence(schema_payload.get("columns"))
    column_names = {
        str(_as_mapping(item).get("name") or "").strip()
        for item in columns
        if str(_as_mapping(item).get("name") or "").strip()
    }

    missing_features = sorted({item for item in required_features if item not in column_names})
    schema_matches = bool(artifact_schema_version) and artifact_schema_version == expected_schema_version

    if shadow_report is None:
        parity_warning_rate = 1.0
    elif shadow_report.total_rows <= 0:
        parity_warning_rate = 1.0
    else:
        parity_warning_rate = shadow_report.rows_with_parity_warnings / max(shadow_report.total_rows, 1)

    passed = schema_matches and not missing_features and parity_warning_rate <= max_parity_warning_rate
    detail = (
        f"artifact_schema={artifact_schema_version or 'unset'} expected_schema={expected_schema_version or 'unset'} "
        f"missing_required_features={len(missing_features)} parity_warning_rate={parity_warning_rate:.6f} "
        f"max_parity_warning_rate={max_parity_warning_rate:.6f}"
    )
    if missing_features:
        detail = f"{detail} missing={','.join(missing_features[:10])}"
    return PromotionCheck(
        name="feature_schema_compatibility",
        passed=passed,
        detail=detail,
        value=parity_warning_rate,
        threshold=max_parity_warning_rate,
    )


def _feature_distribution_drift_signal(
    *,
    persistence: JsonlPersistence,
    run_id: str,
    reference_run_ids: Sequence[str],
    threshold: float,
) -> DriftSignal:
    current_stats = _feature_stats_for_run(persistence, run_id)
    if not current_stats:
        return DriftSignal(
            name="input_feature_distribution_shift",
            status="insufficient_data",
            detail="no_prediction_runtime_features_for_current_run",
        )
    reference_stats = _aggregate_feature_stats(persistence, reference_run_ids)
    if not reference_stats:
        return DriftSignal(
            name="input_feature_distribution_shift",
            status="insufficient_data",
            detail="no_reference_prediction_runtime_features",
        )

    deltas: list[tuple[str, float]] = []
    for name, current in current_stats.items():
        reference = reference_stats.get(name)
        if reference is None:
            continue
        delta = _relative_delta(current, reference)
        deltas.append((name, delta))
    if not deltas:
        return DriftSignal(
            name="input_feature_distribution_shift",
            status="insufficient_data",
            detail="no_overlapping_feature_distribution_stats",
        )

    deltas.sort(key=lambda row: row[1], reverse=True)
    top_name, top_delta = deltas[0]
    status = _status_from_delta(top_delta, threshold)
    top_rows = ", ".join(f"{name}:{delta:.3f}" for name, delta in deltas[:3])
    return DriftSignal(
        name="input_feature_distribution_shift",
        status=status,
        detail=f"top_feature_shift={top_name} top_deltas={top_rows}",
        current_value=top_delta,
        reference_value=0.0,
        threshold=threshold,
    )


def _market_regime_drift_signal(
    *,
    persistence: JsonlPersistence,
    run_id: str,
    reference_run_ids: Sequence[str],
    threshold: float,
) -> DriftSignal:
    current = _market_snapshot_stats(persistence, run_id)
    reference = _aggregate_market_snapshot_stats(persistence, reference_run_ids)
    if not current or not reference:
        return DriftSignal(name="market_regime_shift", status="insufficient_data", detail="missing_market_snapshot_stats")
    vector = [
        _relative_delta(_as_float(current.get("yes_price_mean"), default=0.0) or 0.0, _as_float(reference.get("yes_price_mean"), default=0.0) or 0.0),
        _relative_delta(_as_float(current.get("spread_bps_mean"), default=0.0) or 0.0, _as_float(reference.get("spread_bps_mean"), default=0.0) or 0.0),
        _relative_delta(_as_float(current.get("liquidity_usd_mean"), default=0.0) or 0.0, _as_float(reference.get("liquidity_usd_mean"), default=0.0) or 0.0),
    ]
    drift_score = max(vector) if vector else 0.0
    status = _status_from_delta(drift_score, threshold)
    return DriftSignal(
        name="market_regime_shift",
        status=status,
        detail=f"yes_price_delta={vector[0]:.3f} spread_delta={vector[1]:.3f} liquidity_delta={vector[2]:.3f}",
        current_value=drift_score,
        reference_value=0.0,
        threshold=threshold,
    )

def _research_coverage_drift_signal(
    *,
    persistence: JsonlPersistence,
    run_id: str,
    reference_run_ids: Sequence[str],
    threshold: float,
) -> DriftSignal:
    current = _research_stats(persistence, run_id)
    reference = _aggregate_research_stats(persistence, reference_run_ids)
    if not current or not reference:
        return DriftSignal(
            name="research_coverage_degradation",
            status="insufficient_data",
            detail="missing_research_packet_stats",
        )
    current_findings = _as_float(current.get("findings_mean"), default=0.0) or 0.0
    reference_findings = _as_float(reference.get("findings_mean"), default=0.0) or 0.0
    current_strength = _as_float(current.get("evidence_strength_mean"), default=0.0) or 0.0
    reference_strength = _as_float(reference.get("evidence_strength_mean"), default=0.0) or 0.0
    findings_drop = _degradation_ratio(current_findings, reference_findings)
    strength_drop = _degradation_ratio(current_strength, reference_strength)
    drift_score = max(findings_drop, strength_drop)
    status = _status_from_delta(drift_score, threshold)
    return DriftSignal(
        name="research_coverage_degradation",
        status=status,
        detail=(
            f"findings_drop={findings_drop:.3f} strength_drop={strength_drop:.3f} "
            f"current_findings={current_findings:.3f} reference_findings={reference_findings:.3f}"
        ),
        current_value=drift_score,
        reference_value=0.0,
        threshold=threshold,
    )


def _confidence_collapse_signal(
    *,
    persistence: JsonlPersistence,
    run_id: str,
    reference_run_ids: Sequence[str],
    threshold: float,
    confidence_floor: float,
) -> DriftSignal:
    current = _prediction_confidence_stats(persistence, run_id)
    reference = _aggregate_prediction_confidence_stats(persistence, reference_run_ids)
    if not current or not reference:
        return DriftSignal(
            name="prediction_confidence_collapse",
            status="insufficient_data",
            detail="missing_prediction_confidence_stats",
        )

    current_conf = _as_float(current.get("confidence_mean"), default=0.0) or 0.0
    reference_conf = _as_float(reference.get("confidence_mean"), default=0.0) or 0.0
    confidence_drop = _degradation_ratio(current_conf, reference_conf)

    current_approval = _as_float(current.get("approval_rate"), default=0.0) or 0.0
    reference_approval = _as_float(reference.get("approval_rate"), default=0.0) or 0.0
    approval_drop = _degradation_ratio(current_approval, reference_approval)

    drift_score = max(confidence_drop, approval_drop)
    status = _status_from_delta(drift_score, threshold)
    if current_conf < confidence_floor and status != "insufficient_data":
        status = "critical"
    return DriftSignal(
        name="prediction_confidence_collapse",
        status=status,
        detail=(
            f"confidence_drop={confidence_drop:.3f} approval_drop={approval_drop:.3f} "
            f"current_confidence={current_conf:.3f} reference_confidence={reference_conf:.3f}"
        ),
        current_value=drift_score,
        reference_value=0.0,
        threshold=threshold,
    )


def _load_model_artifact(path_text: str) -> Mapping[str, Any]:
    path = Path(path_text.strip()) if path_text.strip() else Path()
    if not path or not path.exists():
        return {}
    return _load_json_mapping(path)


def _load_benchmark_payload(*, derived_root: Path, run_id: str | None) -> Mapping[str, Any]:
    if run_id:
        path = derived_root / "benchmark_runs" / f"{run_id}.json"
    else:
        path = derived_root / "latest_benchmark_run.json"
    return _load_json_mapping(path)


def _load_training_payload(*, derived_root: Path, run_id: str | None) -> Mapping[str, Any]:
    if run_id:
        path = derived_root / "training_lab" / "runs" / run_id / "summary.json"
    else:
        path = derived_root / "training_lab" / "latest_training_run.json"
    return _load_json_mapping(path)


def _load_calibration_payload(*, derived_root: Path) -> Mapping[str, Any]:
    latest = _load_json_mapping(derived_root / "training_lab" / "latest_calibration_run.json")
    run_id = str(latest.get("run_id") or "").strip()
    if not run_id:
        return {}
    return _load_json_mapping(derived_root / "training_lab" / "calibration_runs" / run_id / "summary.json")


def _load_walk_forward_payload(*, derived_root: Path, run_id: str | None) -> Mapping[str, Any]:
    if run_id:
        path = derived_root / "strategy_walk_forward_runs" / f"{run_id}.json"
    else:
        path = derived_root / "latest_strategy_walk_forward_run.json"
    return _load_json_mapping(path)


def _resolve_latest_run_id(persistence: JsonlPersistence) -> str:
    run_ids = list_run_ids(persistence, limit_runs=1)
    if not run_ids:
        return ""
    return run_ids[-1]


def _resolve_latest_shadow_run_id(persistence: JsonlPersistence) -> str:
    run_ids = list_run_ids(persistence, limit_runs=50)
    for run_id in reversed(run_ids):
        report = build_shadow_scoring_report(persistence, run_id)
        if report.total_rows > 0:
            return run_id
    return ""


def _feature_stats_for_run(persistence: JsonlPersistence, run_id: str) -> dict[str, float]:
    rows = persistence.read_artifact_records(run_id, "prediction_runtime_features")
    samples: dict[str, list[float]] = {}
    for row in rows:
        payload = _as_mapping(row.get("payload"))
        values = _as_mapping(payload.get("values"))
        for name, raw in values.items():
            feature_name = str(name).strip()
            if not feature_name.startswith("f_"):
                continue
            numeric = _as_float(raw)
            if numeric is None:
                continue
            samples.setdefault(feature_name, []).append(numeric)
    return {name: mean(values) for name, values in samples.items() if values}


def _aggregate_feature_stats(persistence: JsonlPersistence, run_ids: Sequence[str]) -> dict[str, float]:
    aggregate: dict[str, list[float]] = {}
    for run_id in run_ids:
        for name, value in _feature_stats_for_run(persistence, run_id).items():
            aggregate.setdefault(name, []).append(value)
    return {name: mean(values) for name, values in aggregate.items() if values}


def _market_snapshot_stats(persistence: JsonlPersistence, run_id: str) -> dict[str, float]:
    rows = persistence.read_artifact_records(run_id, "market_snapshots")
    yes_prices: list[float] = []
    spreads: list[float] = []
    liquidities: list[float] = []
    for row in rows:
        payload = _as_mapping(row.get("payload"))
        yes = _as_float(payload.get("yes_price"))
        spread = _as_float(payload.get("spread_bps"))
        liquidity = _as_float(payload.get("liquidity_usd"))
        if yes is not None:
            yes_prices.append(yes)
        if spread is not None:
            spreads.append(spread)
        if liquidity is not None:
            liquidities.append(liquidity)
    if not yes_prices or not spreads or not liquidities:
        return {}
    return {
        "yes_price_mean": mean(yes_prices),
        "spread_bps_mean": mean(spreads),
        "liquidity_usd_mean": mean(liquidities),
    }


def _aggregate_market_snapshot_stats(persistence: JsonlPersistence, run_ids: Sequence[str]) -> dict[str, float]:
    bucket: dict[str, list[float]] = {}
    for run_id in run_ids:
        stats = _market_snapshot_stats(persistence, run_id)
        for key, value in stats.items():
            bucket.setdefault(key, []).append(value)
    return {key: mean(values) for key, values in bucket.items() if values}


def _research_stats(persistence: JsonlPersistence, run_id: str) -> dict[str, float]:
    rows = persistence.read_artifact_records(run_id, "research_packets")
    finding_counts: list[float] = []
    evidence_strength: list[float] = []
    for row in rows:
        payload = _as_mapping(row.get("payload"))
        findings = _as_sequence(payload.get("findings"))
        finding_counts.append(float(len(findings)))
        strength = _as_float(payload.get("evidence_strength"))
        if strength is not None:
            evidence_strength.append(strength)
    if not finding_counts or not evidence_strength:
        return {}
    return {
        "findings_mean": mean(finding_counts),
        "evidence_strength_mean": mean(evidence_strength),
    }


def _aggregate_research_stats(persistence: JsonlPersistence, run_ids: Sequence[str]) -> dict[str, float]:
    bucket: dict[str, list[float]] = {}
    for run_id in run_ids:
        stats = _research_stats(persistence, run_id)
        for key, value in stats.items():
            bucket.setdefault(key, []).append(value)
    return {key: mean(values) for key, values in bucket.items() if values}


def _prediction_confidence_stats(persistence: JsonlPersistence, run_id: str) -> dict[str, float]:
    prediction_rows = persistence.read_artifact_records(run_id, "prediction_results")
    confidences: list[float] = []
    for row in prediction_rows:
        payload = _as_mapping(row.get("payload"))
        confidence = _as_float(payload.get("confidence"))
        if confidence is not None:
            confidences.append(confidence)
    risk_rows = persistence.read_artifact_records(run_id, "effective_risk_decisions")
    if not risk_rows:
        risk_rows = persistence.read_artifact_records(run_id, "risk_decisions")
    approvals: list[float] = []
    for row in risk_rows:
        payload = _as_mapping(row.get("payload"))
        approved = payload.get("approved")
        if isinstance(approved, bool):
            approvals.append(1.0 if approved else 0.0)
    if not confidences or not approvals:
        return {}
    return {
        "confidence_mean": mean(confidences),
        "approval_rate": mean(approvals),
    }


def _aggregate_prediction_confidence_stats(persistence: JsonlPersistence, run_ids: Sequence[str]) -> dict[str, float]:
    bucket: dict[str, list[float]] = {}
    for run_id in run_ids:
        stats = _prediction_confidence_stats(persistence, run_id)
        for key, value in stats.items():
            bucket.setdefault(key, []).append(value)
    return {key: mean(values) for key, values in bucket.items() if values}


def _status_from_delta(delta: float, threshold: float) -> str:
    if delta <= threshold:
        return "ok"
    if delta <= threshold * 2.0:
        return "warning"
    return "critical"


def _worst_status(statuses: Sequence[str]) -> str:
    order = {"ok": 0, "insufficient_data": 1, "warning": 2, "critical": 3}
    if not statuses:
        return "insufficient_data"
    return max(statuses, key=lambda item: order.get(item, 1))


def _relative_delta(current: float, reference: float) -> float:
    baseline = abs(reference) if abs(reference) > 1e-9 else 1.0
    return abs(current - reference) / baseline


def _degradation_ratio(current: float, reference: float) -> float:
    if reference <= 1e-9:
        return 0.0
    if current >= reference:
        return 0.0
    return (reference - current) / reference


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, Mapping):
        return raw
    return {}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _as_float(value: Any, *, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default
