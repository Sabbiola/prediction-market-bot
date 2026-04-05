from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.agents.prediction_heuristic import (
    disagreement_bucket,
    prediction_approved,
    run_heuristic,
)
from prediction_market_bot.agents.prediction_model_runtime import RuntimePredictionModelContract
from prediction_market_bot.agents.prediction_runtime_features import (
    RuntimePredictionFeatures,
    build_alt_shadow_parity_warnings,
    build_alt_shadow_prediction_features,
)
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket

# Type alias for the _predict_with_model_contract callable
PredictContractFn = Callable[..., tuple[PredictionResult, tuple[str, ...], float]]


@dataclass(slots=True)
class ShadowModeResult:
    prediction: PredictionResult
    shadow_comparison: dict[str, object]
    parity_warnings: tuple[str, ...]


def run_shadow_mode(
    settings: PredictionSettings,
    runtime_model: RuntimePredictionModelContract | None,
    model_load_error: str,
    alt_shadow_model: RuntimePredictionModelContract | None,
    alt_shadow_model_load_error: str,
    candidate: MarketCandidate,
    research: ResearchPacket,
    runtime_features: RuntimePredictionFeatures,
    predict_fn: PredictContractFn,
) -> ShadowModeResult:
    """Run shadow mode: heuristic primary, model_v2 and alt_llm shadow."""
    primary = run_heuristic(settings, candidate, research)
    parity_warnings: list[str] = []
    model_result: PredictionResult | None = None
    alt_shadow_result: PredictionResult | None = None
    model_status = "not_requested"
    alt_shadow_status = "not_requested"
    model_path_warnings: list[str] = []
    alt_shadow_path_warnings: list[str] = []

    if runtime_model is None:
        model_status = "model_unavailable"
        reason = model_load_error or "runtime_model_not_loaded"
        model_path_warnings.append(reason)
    else:
        try:
            model_result, parity_errors, _ = predict_fn(
                candidate,
                research,
                contract=runtime_model,
                strict_parity=settings.strict_feature_parity,
                engine_label="model_v2_shadow",
                runtime_features=runtime_features,
                expected_feature_schema_version=settings.model_expected_feature_schema_version,
            )
            if parity_errors:
                model_path_warnings.extend(parity_errors)
            model_status = "available" if not parity_errors else "available_with_parity_warnings"
        except Exception as exc:
            model_status = "inference_error"
            model_path_warnings.append(f"shadow_model_error reason={exc}")

    if settings.alt_shadow_enabled:
        alt_features = build_alt_shadow_prediction_features(
            candidate,
            research,
            base_features=runtime_features,
            schema_version=settings.alt_shadow_expected_feature_schema_version,
        )
        coverage_warnings = build_alt_shadow_parity_warnings(
            alt_features.values,
            required_sources=settings.alt_shadow_required_source_coverage,
            require_llm_enrichment=settings.alt_shadow_require_llm_enrichment,
        )
        if coverage_warnings:
            alt_shadow_path_warnings.extend(coverage_warnings)

        if alt_shadow_model is None:
            alt_shadow_status = "model_unavailable"
            reason = alt_shadow_model_load_error or "alt_shadow_runtime_model_not_loaded"
            alt_shadow_path_warnings.append(reason)
        else:
            try:
                alt_shadow_result, alt_parity_errors, _ = predict_fn(
                    candidate,
                    research,
                    contract=alt_shadow_model,
                    strict_parity=settings.alt_shadow_strict_feature_parity,
                    engine_label="alt_llm_shadow",
                    runtime_features=alt_features,
                    expected_feature_schema_version=settings.alt_shadow_expected_feature_schema_version,
                )
                if alt_parity_errors:
                    alt_shadow_path_warnings.extend(alt_parity_errors)
                alt_shadow_status = (
                    "available" if not alt_shadow_path_warnings else "available_with_parity_warnings"
                )
            except Exception as exc:
                alt_shadow_status = "inference_error"
                alt_shadow_path_warnings.append(f"alt_llm_shadow_error reason={exc}")

    parity_warnings.extend(model_path_warnings)
    parity_warnings.extend(alt_shadow_path_warnings)

    heuristic_approved = prediction_approved(
        primary,
        min_confidence=settings.min_confidence,
        min_edge_bps=float(settings.min_edge_bps),
    )
    model_approved = (
        prediction_approved(model_result, min_confidence=settings.min_confidence, min_edge_bps=float(settings.min_edge_bps))
        if model_result is not None
        else None
    )
    alt_shadow_approved = (
        prediction_approved(alt_shadow_result, min_confidence=settings.min_confidence, min_edge_bps=float(settings.min_edge_bps))
        if alt_shadow_result is not None
        else None
    )
    fair_yes_delta = (model_result.fair_yes_prob - primary.fair_yes_prob) if model_result is not None else None
    edge_delta = (model_result.edge - primary.edge) if model_result is not None else None
    confidence_delta = (model_result.confidence - primary.confidence) if model_result is not None else None
    alt_fair_yes_delta = (
        alt_shadow_result.fair_yes_prob - primary.fair_yes_prob
        if alt_shadow_result is not None
        else None
    )
    alt_edge_delta = (alt_shadow_result.edge - primary.edge) if alt_shadow_result is not None else None
    alt_confidence_delta = (
        alt_shadow_result.confidence - primary.confidence if alt_shadow_result is not None else None
    )
    shadow_bucket = disagreement_bucket(
        fair_yes_delta=fair_yes_delta,
        selected_side_changed=(
            bool(model_result is not None and model_result.selected_side != primary.selected_side)
        ),
    )
    if settings.alt_shadow_enabled:
        alt_disagreement_bucket = disagreement_bucket(
            fair_yes_delta=alt_fair_yes_delta,
            selected_side_changed=(
                bool(alt_shadow_result is not None and alt_shadow_result.selected_side != primary.selected_side)
            ),
        )
    else:
        alt_disagreement_bucket = "not_requested"

    parity_status = "ok" if not parity_warnings else "warning"
    shadow_comparison: dict[str, object] = {
        "artifact_version": "v2",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "market_id": candidate.market.market_id,
        "heuristic_prediction": primary.model_dump(mode="json"),
        "model_v2_prediction": model_result.model_dump(mode="json") if model_result is not None else None,
        "alt_llm_shadow_prediction": (
            alt_shadow_result.model_dump(mode="json") if alt_shadow_result is not None else None
        ),
        "primary_prediction": "heuristic",
        "model_v2_status": model_status,
        "alt_llm_shadow_status": alt_shadow_status,
        "parity_status": parity_status,
        "parity_warnings": list(parity_warnings),
        "parity_warnings_by_path": {
            "model_v2": list(model_path_warnings),
            "alt_llm_shadow": list(alt_shadow_path_warnings),
        },
        "deltas": {
            "fair_yes_prob_delta": fair_yes_delta,
            "edge_delta": edge_delta,
            "confidence_delta": confidence_delta,
            "selected_side_changed": (
                bool(model_result is not None and model_result.selected_side != primary.selected_side)
            ),
            "approval_changed": (
                bool(model_approved is not None and model_approved != heuristic_approved)
            ),
            "alt_llm_fair_yes_prob_delta": alt_fair_yes_delta,
            "alt_llm_edge_delta": alt_edge_delta,
            "alt_llm_confidence_delta": alt_confidence_delta,
            "alt_llm_selected_side_changed": (
                bool(alt_shadow_result is not None and alt_shadow_result.selected_side != primary.selected_side)
            ),
            "alt_llm_approval_changed": (
                bool(alt_shadow_approved is not None and alt_shadow_approved != heuristic_approved)
            ),
        },
        "approvals": {
            "min_confidence": settings.min_confidence,
            "min_edge_bps": settings.min_edge_bps,
            "heuristic": heuristic_approved,
            "model_v2": model_approved,
            "alt_llm_shadow": alt_shadow_approved,
        },
        "disagreement_bucket": shadow_bucket,
        "alt_llm_disagreement_bucket": alt_disagreement_bucket,
    }

    appended_rationale = (
        *primary.rationale,
        "prediction_engine=shadow_primary_heuristic",
        f"shadow_model_status={model_status}",
        f"shadow_alt_llm_status={alt_shadow_status}",
        f"shadow_disagreement_bucket={shadow_bucket}",
        f"shadow_alt_llm_disagreement_bucket={alt_disagreement_bucket}",
        f"shadow_fair_yes_prob_delta={fair_yes_delta if fair_yes_delta is not None else 'n/a'}",
        f"shadow_alt_llm_fair_yes_prob_delta={alt_fair_yes_delta if alt_fair_yes_delta is not None else 'n/a'}",
        f"shadow_edge_delta={edge_delta if edge_delta is not None else 'n/a'}",
        f"shadow_alt_llm_edge_delta={alt_edge_delta if alt_edge_delta is not None else 'n/a'}",
        f"shadow_approval_heuristic={heuristic_approved}",
        f"shadow_approval_model_v2={model_approved if model_approved is not None else 'n/a'}",
        f"shadow_approval_alt_llm={alt_shadow_approved if alt_shadow_approved is not None else 'n/a'}",
        f"shadow_parity_status={parity_status}",
    )
    if parity_warnings:
        for warning in parity_warnings:
            appended_rationale = (*appended_rationale, f"shadow_parity_warning={warning}")

    final_prediction = primary.model_copy(update={"rationale": tuple(appended_rationale)})
    return ShadowModeResult(
        prediction=final_prediction,
        shadow_comparison=shadow_comparison,
        parity_warnings=tuple(parity_warnings),
    )
