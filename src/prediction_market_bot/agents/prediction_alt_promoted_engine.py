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
from prediction_market_bot.agents.prediction_model_runtime import (
    PredictionModelArtifactError,
    RuntimePredictionModelContract,
)
from prediction_market_bot.agents.prediction_runtime_features import (
    RuntimePredictionFeatures,
    build_alt_shadow_parity_warnings,
    build_alt_shadow_prediction_features,
)
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket

# Type alias for the _predict_with_model_contract callable
PredictContractFn = Callable[..., tuple[PredictionResult, tuple[str, ...], float]]


@dataclass(slots=True)
class AltPromotedResult:
    prediction: PredictionResult
    shadow_comparison: dict[str, object]
    parity_warnings: tuple[str, ...]


def run_alt_promoted_mode(
    settings: PredictionSettings,
    runtime_model: RuntimePredictionModelContract | None,
    model_load_error: str,
    alt_shadow_model: RuntimePredictionModelContract | None,
    alt_shadow_model_load_error: str,
    candidate: MarketCandidate,
    research: ResearchPacket,
    runtime_features: RuntimePredictionFeatures,
    predict_fn: PredictContractFn,
) -> AltPromotedResult:
    """Run alt_llm_promoted mode vs model_v2 baseline."""
    if runtime_model is None:
        reason = model_load_error or "runtime_model_not_loaded"
        if not settings.fallback_to_heuristic:
            raise PredictionModelArtifactError(reason)
        base = run_heuristic(settings, candidate, research)
        fallback_prediction = base.model_copy(
            update={
                "rationale": tuple((*base.rationale, f"prediction_engine_fallback=heuristic reason={reason}")),
            }
        )
        # Return a minimal result; the caller should handle fallback uniformly,
        # but to preserve original behaviour we raise or return the fallback here.
        # Original code raised/returned via _fallback_or_raise, so we replicate that:
        return AltPromotedResult(
            prediction=fallback_prediction,
            shadow_comparison={},
            parity_warnings=(),
        )

    try:
        baseline_prediction, baseline_parity_errors, _ = predict_fn(
            candidate,
            research,
            contract=runtime_model,
            strict_parity=settings.strict_feature_parity,
            engine_label="model_v2_baseline",
            runtime_features=runtime_features,
        )
    except Exception as exc:
        reason = f"model_v2_baseline_inference_failed reason={exc}"
        if not settings.fallback_to_heuristic:
            raise PredictionModelArtifactError(reason)
        base = run_heuristic(settings, candidate, research)
        fallback_prediction = base.model_copy(
            update={
                "rationale": tuple((*base.rationale, f"prediction_engine_fallback=heuristic reason={reason}")),
            }
        )
        return AltPromotedResult(
            prediction=fallback_prediction,
            shadow_comparison={},
            parity_warnings=(),
        )

    alt_features = build_alt_shadow_prediction_features(
        candidate,
        research,
        base_features=runtime_features,
        schema_version=settings.alt_shadow_expected_feature_schema_version,
    )
    warnings: list[str] = list(baseline_parity_errors)
    coverage_warnings = build_alt_shadow_parity_warnings(
        alt_features.values,
        required_sources=settings.alt_shadow_required_source_coverage,
        require_llm_enrichment=settings.alt_shadow_require_llm_enrichment,
    )
    warnings.extend(coverage_warnings)

    alt_status = "model_unavailable"
    alt_prediction: PredictionResult | None = None
    if alt_shadow_model is None:
        warnings.append(alt_shadow_model_load_error or "alt_llm_shadow_runtime_model_not_loaded")
    else:
        try:
            alt_prediction, alt_parity_errors, _ = predict_fn(
                candidate,
                research,
                contract=alt_shadow_model,
                strict_parity=settings.alt_shadow_strict_feature_parity,
                engine_label="alt_llm_promoted",
                runtime_features=alt_features,
                expected_feature_schema_version=settings.alt_shadow_expected_feature_schema_version,
            )
            if alt_parity_errors:
                warnings.extend(alt_parity_errors)
                alt_status = "available_with_parity_warnings"
            else:
                alt_status = "available"
        except Exception as exc:
            alt_status = "inference_error"
            warnings.append(f"alt_llm_promoted_inference_error reason={exc}")

    can_use_alt_path = (
        alt_prediction is not None
        and alt_status == "available"
        and not coverage_warnings
    )
    final_prediction = baseline_prediction
    if can_use_alt_path:
        assert alt_prediction is not None
        final_prediction = alt_prediction
        final_prediction = final_prediction.model_copy(
            update={
                "rationale": tuple(
                    (
                        *final_prediction.rationale,
                        "alt_llm_promoted_active=true",
                        "alt_llm_promoted_fallback=none",
                    )
                ),
            }
        )
    else:
        fallback_reason = "alt_data_or_llm_path_unavailable"
        final_prediction = baseline_prediction.model_copy(
            update={
                "rationale": tuple(
                    (
                        *baseline_prediction.rationale,
                        "alt_llm_promoted_active=false",
                        f"alt_llm_promoted_fallback=model_v2 reason={fallback_reason}",
                    )
                ),
            }
        )
        if alt_status == "available" and coverage_warnings:
            alt_status = "coverage_missing"

    baseline_approved = prediction_approved(
        baseline_prediction,
        min_confidence=settings.min_confidence,
        min_edge_bps=float(settings.min_edge_bps),
    )
    alt_approved = prediction_approved(
        alt_prediction,
        min_confidence=settings.min_confidence,
        min_edge_bps=float(settings.min_edge_bps),
    )
    fair_yes_delta = (
        (alt_prediction.fair_yes_prob - baseline_prediction.fair_yes_prob)
        if alt_prediction is not None
        else None
    )
    edge_delta = (
        (alt_prediction.edge - baseline_prediction.edge)
        if alt_prediction is not None
        else None
    )
    confidence_delta = (
        (alt_prediction.confidence - baseline_prediction.confidence)
        if alt_prediction is not None
        else None
    )
    bucket = disagreement_bucket(
        fair_yes_delta=fair_yes_delta,
        selected_side_changed=(
            bool(alt_prediction is not None and alt_prediction.selected_side != baseline_prediction.selected_side)
        ),
    )
    deduped_warnings = tuple(dict.fromkeys(item for item in warnings if item.strip()))
    parity_status = "ok" if not deduped_warnings else "warning"

    shadow_comparison: dict[str, object] = {
        "comparison_kind": "alt_promoted_vs_model_v2_baseline",
        "artifact_version": "v2",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "market_id": candidate.market.market_id,
        "primary_prediction": "alt_llm_promoted" if can_use_alt_path else "model_v2",
        "model_v2_prediction": baseline_prediction.model_dump(mode="json"),
        "alt_llm_promoted_prediction": (
            alt_prediction.model_dump(mode="json") if alt_prediction is not None else None
        ),
        "alt_llm_promoted_status": alt_status,
        "parity_status": parity_status,
        "parity_warnings": list(deduped_warnings),
        "approvals": {
            "min_confidence": settings.min_confidence,
            "min_edge_bps": settings.min_edge_bps,
            "model_v2": baseline_approved,
            "alt_llm_promoted": alt_approved,
        },
        "deltas": {
            "fair_yes_prob_delta_alt_minus_model_v2": fair_yes_delta,
            "edge_delta_alt_minus_model_v2": edge_delta,
            "confidence_delta_alt_minus_model_v2": confidence_delta,
            "selected_side_changed": (
                bool(alt_prediction is not None and alt_prediction.selected_side != baseline_prediction.selected_side)
            ),
            "approval_changed": (
                bool(alt_approved is not None and baseline_approved is not None and alt_approved != baseline_approved)
            ),
        },
        "disagreement_bucket": bucket,
        "enrichment_coverage": alt_features.values.get("f_alt_enrichment_coverage", 0.0),
        "active_source_set": list(settings.alt_shadow_required_source_coverage),
    }

    if deduped_warnings:
        final_prediction = final_prediction.model_copy(
            update={
                "rationale": tuple(
                    (
                        *final_prediction.rationale,
                        *tuple(f"alt_llm_promoted_warning={item}" for item in deduped_warnings),
                    )
                ),
            }
        )

    return AltPromotedResult(
        prediction=final_prediction,
        shadow_comparison=shadow_comparison,
        parity_warnings=deduped_warnings,
    )
