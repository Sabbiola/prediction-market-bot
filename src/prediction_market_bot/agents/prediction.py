from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.agents.prediction_heuristic import (
    clamp_01,
    clamp_probability,
    disagreement_bucket,
    edge_for_side,
    prediction_approved,
    prediction_from_fair_probability,
    run_heuristic,
)
from prediction_market_bot.agents.prediction_model_runtime import (
    PredictionModelArtifactError,
    PredictionModelArtifactLoader,
    RuntimePredictionModelContract,
)
from prediction_market_bot.agents.prediction_runtime_features import (
    RuntimePredictionFeatures,
    build_alt_shadow_parity_warnings,
    build_alt_shadow_prediction_features,
    build_runtime_prediction_features,
)
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket


class PredictionAgent:
    name = "prediction-agent"

    def __init__(self, settings: PredictionSettings) -> None:
        self.settings = settings
        self._artifact_loader = PredictionModelArtifactLoader()
        self._runtime_model: RuntimePredictionModelContract | None = None
        self._model_load_error = ""
        self._alt_shadow_runtime_model: RuntimePredictionModelContract | None = None
        self._alt_shadow_model_load_error = ""
        self.last_shadow_comparison: dict[str, object] | None = None
        self.last_parity_warnings: tuple[str, ...] = ()
        self.last_runtime_features: dict[str, object] | None = None
        self._load_runtime_model()

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        self.last_shadow_comparison = None
        self.last_parity_warnings = ()
        runtime_features = build_runtime_prediction_features(candidate, research)
        self.last_runtime_features = {
            "schema_version": runtime_features.schema_version,
            "decision_timestamp_utc": runtime_features.decision_timestamp_utc.isoformat(),
            "values": dict(runtime_features.values),
        }
        if self._use_shadow_mode():
            return self._run_shadow_mode(candidate, research, runtime_features=runtime_features)
        if self._use_alt_promoted_mode():
            return self._run_model_v2_alt_promoted_or_fallback(candidate, research, runtime_features=runtime_features)
        if self._use_model_v2():
            return self._run_model_v2_or_fallback(candidate, research, runtime_features=runtime_features)
        return run_heuristic(self.settings, candidate, research)

    def _load_runtime_model(self) -> None:
        if not self._requires_runtime_model():
            return
        self._runtime_model, self._model_load_error = self._load_model_contract(
            model_artifact_path=self.settings.model_artifact_path,
            calibration_artifact_path=self.settings.calibration_artifact_path,
            empty_error_reason="model_v2_enabled_but_model_artifact_path_is_empty",
            fallback_to_heuristic=self.settings.fallback_to_heuristic,
        )
        if self.settings.alt_shadow_enabled and (self._use_shadow_mode() or self._use_alt_promoted_mode()):
            self._alt_shadow_runtime_model, self._alt_shadow_model_load_error = self._load_model_contract(
                model_artifact_path=self.settings.alt_shadow_model_artifact_path,
                calibration_artifact_path=self.settings.alt_shadow_calibration_artifact_path,
                empty_error_reason="alt_llm_shadow_enabled_but_model_artifact_path_is_empty",
                fallback_to_heuristic=True,
            )

    def _load_model_contract(
        self,
        *,
        model_artifact_path: str,
        calibration_artifact_path: str,
        empty_error_reason: str,
        fallback_to_heuristic: bool,
    ) -> tuple[RuntimePredictionModelContract | None, str]:
        artifact_path = model_artifact_path.strip()
        if not artifact_path:
            if fallback_to_heuristic:
                return None, empty_error_reason
            raise PredictionModelArtifactError(empty_error_reason)
        calibration_path = calibration_artifact_path.strip() or None
        try:
            loaded = self._artifact_loader.load(
                Path(artifact_path),
                calibration_artifact_path=Path(calibration_path) if calibration_path else None,
            )
        except PredictionModelArtifactError as exc:
            reason = f"model_artifact_load_failed reason={exc}"
            if fallback_to_heuristic:
                return None, reason
            raise
        return loaded, ""

    def _run_model_v2_or_fallback(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        if self._runtime_model is None:
            return self._fallback_or_raise(
                candidate,
                research,
                reason=self._model_load_error or "runtime_model_not_loaded",
            )
        try:
            return self._run_model_v2(candidate, research, self._runtime_model, runtime_features=runtime_features)
        except Exception as exc:
            return self._fallback_or_raise(
                candidate,
                research,
                reason=f"model_v2_inference_failed reason={exc}",
            )

    def _run_model_v2_alt_promoted_or_fallback(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        if self._runtime_model is None:
            return self._fallback_or_raise(
                candidate,
                research,
                reason=self._model_load_error or "runtime_model_not_loaded",
            )
        try:
            baseline_prediction, baseline_parity_errors, _ = self._predict_with_model_contract(
                candidate,
                research,
                contract=self._runtime_model,
                strict_parity=self.settings.strict_feature_parity,
                engine_label="model_v2_baseline",
                runtime_features=runtime_features,
            )
        except Exception as exc:
            return self._fallback_or_raise(
                candidate,
                research,
                reason=f"model_v2_baseline_inference_failed reason={exc}",
            )

        alt_features = build_alt_shadow_prediction_features(
            candidate,
            research,
            base_features=runtime_features,
            schema_version=self.settings.alt_shadow_expected_feature_schema_version,
        )
        warnings: list[str] = list(baseline_parity_errors)
        coverage_warnings = build_alt_shadow_parity_warnings(
            alt_features.values,
            required_sources=self.settings.alt_shadow_required_source_coverage,
            require_llm_enrichment=self.settings.alt_shadow_require_llm_enrichment,
        )
        warnings.extend(coverage_warnings)

        alt_status = "model_unavailable"
        alt_prediction: PredictionResult | None = None
        if self._alt_shadow_runtime_model is None:
            warnings.append(self._alt_shadow_model_load_error or "alt_llm_shadow_runtime_model_not_loaded")
        else:
            try:
                alt_prediction, alt_parity_errors, _ = self._predict_with_model_contract(
                    candidate,
                    research,
                    contract=self._alt_shadow_runtime_model,
                    strict_parity=self.settings.alt_shadow_strict_feature_parity,
                    engine_label="alt_llm_promoted",
                    runtime_features=alt_features,
                    expected_feature_schema_version=self.settings.alt_shadow_expected_feature_schema_version,
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

        baseline_approved = prediction_approved(baseline_prediction, min_confidence=self.settings.min_confidence, min_edge_bps=float(self.settings.min_edge_bps))
        alt_approved = prediction_approved(alt_prediction, min_confidence=self.settings.min_confidence, min_edge_bps=float(self.settings.min_edge_bps))
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
        self.last_parity_warnings = deduped_warnings
        self.last_shadow_comparison = {
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
                "min_confidence": self.settings.min_confidence,
                "min_edge_bps": self.settings.min_edge_bps,
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
            "active_source_set": list(self.settings.alt_shadow_required_source_coverage),
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
        return final_prediction

    def _fallback_or_raise(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        reason: str,
    ) -> PredictionResult:
        if not self.settings.fallback_to_heuristic:
            raise PredictionModelArtifactError(reason)
        base = run_heuristic(self.settings, candidate, research)
        return base.model_copy(
            update={
                "rationale": tuple((*base.rationale, f"prediction_engine_fallback=heuristic reason={reason}")),
            }
        )

    def _run_model_v2(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        contract: RuntimePredictionModelContract,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        prediction, _, _ = self._predict_with_model_contract(
            candidate,
            research,
            contract=contract,
            strict_parity=self.settings.strict_feature_parity,
            engine_label="model_v2",
            runtime_features=runtime_features,
        )
        return prediction

    def _predict_with_model_contract(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        contract: RuntimePredictionModelContract,
        strict_parity: bool,
        engine_label: str,
        runtime_features: RuntimePredictionFeatures | None = None,
        expected_feature_schema_version: str | None = None,
    ) -> tuple[PredictionResult, tuple[str, ...], float]:
        features = runtime_features if runtime_features is not None else build_runtime_prediction_features(candidate, research)
        expected_schema = (
            expected_feature_schema_version
            if expected_feature_schema_version is not None
            else self.settings.model_expected_feature_schema_version
        ).strip()
        parity_errors: list[str] = []
        if expected_schema and contract.feature_schema_version != expected_schema:
            parity_errors.append(
                "feature_schema_version_mismatch "
                f"artifact={contract.feature_schema_version} expected={expected_schema} runtime={features.schema_version}"
            )
        parity_errors.extend(
            contract.parity_errors(
                runtime_feature_schema_version=features.schema_version,
                runtime_features=features.values,
            )
        )
        parity_tuple = tuple(parity_errors)
        if parity_tuple and strict_parity:
            raise PredictionModelArtifactError(f"feature_parity_check_failed {'; '.join(parity_tuple)}")

        raw_yes_prob, calibrated_yes_prob = contract.predict_yes_probability(features.values)
        fair_yes_prob = clamp_probability(calibrated_yes_prob)
        confidence = self._model_confidence(
            fair_yes_prob=fair_yes_prob,
            edge=edge_for_side(candidate=candidate, fair_yes_prob=fair_yes_prob),
            candidate=candidate,
            features=features.values,
        )
        parity_summary = "ok" if not parity_tuple else f"non_strict:{'|'.join(parity_tuple)}"
        rationale = (
            f"prediction_engine={engine_label}",
            f"model_name={contract.model_name}",
            f"model_version={contract.model_version}",
            f"calibration_version={contract.calibration.calibration_version}",
            f"calibration_method={contract.calibration.method}",
            f"feature_schema_version={features.schema_version}",
            f"artifact_feature_schema_version={contract.feature_schema_version}",
            f"feature_parity={parity_summary}",
            f"raw_yes_prob={raw_yes_prob:.4f}",
            f"calibrated_yes_prob={fair_yes_prob:.4f}",
            f"confidence={confidence:.4f}",
        )
        prediction = prediction_from_fair_probability(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            confidence=confidence,
            rationale=rationale,
        )
        return prediction, parity_tuple, raw_yes_prob

    def _run_shadow_mode(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        primary = run_heuristic(self.settings, candidate, research)
        parity_warnings: list[str] = []
        model_result: PredictionResult | None = None
        alt_shadow_result: PredictionResult | None = None
        model_status = "not_requested"
        alt_shadow_status = "not_requested"
        model_path_warnings: list[str] = []
        alt_shadow_path_warnings: list[str] = []
        if self._runtime_model is None:
            model_status = "model_unavailable"
            reason = self._model_load_error or "runtime_model_not_loaded"
            model_path_warnings.append(reason)
        else:
            try:
                model_result, parity_errors, _ = self._predict_with_model_contract(
                    candidate,
                    research,
                    contract=self._runtime_model,
                    strict_parity=self.settings.strict_feature_parity,
                    engine_label="model_v2_shadow",
                    runtime_features=runtime_features,
                    expected_feature_schema_version=self.settings.model_expected_feature_schema_version,
                )
                if parity_errors:
                    model_path_warnings.extend(parity_errors)
                model_status = "available" if not parity_errors else "available_with_parity_warnings"
            except Exception as exc:
                model_status = "inference_error"
                model_path_warnings.append(f"shadow_model_error reason={exc}")

        if self.settings.alt_shadow_enabled:
            alt_features = build_alt_shadow_prediction_features(
                candidate,
                research,
                base_features=runtime_features,
                schema_version=self.settings.alt_shadow_expected_feature_schema_version,
            )
            coverage_warnings = build_alt_shadow_parity_warnings(
                alt_features.values,
                required_sources=self.settings.alt_shadow_required_source_coverage,
                require_llm_enrichment=self.settings.alt_shadow_require_llm_enrichment,
            )
            if coverage_warnings:
                alt_shadow_path_warnings.extend(coverage_warnings)

            if self._alt_shadow_runtime_model is None:
                alt_shadow_status = "model_unavailable"
                reason = self._alt_shadow_model_load_error or "alt_shadow_runtime_model_not_loaded"
                alt_shadow_path_warnings.append(reason)
            else:
                try:
                    alt_shadow_result, alt_parity_errors, _ = self._predict_with_model_contract(
                        candidate,
                        research,
                        contract=self._alt_shadow_runtime_model,
                        strict_parity=self.settings.alt_shadow_strict_feature_parity,
                        engine_label="alt_llm_shadow",
                        runtime_features=alt_features,
                        expected_feature_schema_version=self.settings.alt_shadow_expected_feature_schema_version,
                    )
                    if alt_parity_errors:
                        alt_shadow_path_warnings.extend(alt_parity_errors)
                    alt_shadow_status = "available" if not alt_shadow_path_warnings else "available_with_parity_warnings"
                except Exception as exc:
                    alt_shadow_status = "inference_error"
                    alt_shadow_path_warnings.append(f"alt_llm_shadow_error reason={exc}")

        parity_warnings.extend(model_path_warnings)
        parity_warnings.extend(alt_shadow_path_warnings)

        heuristic_approved = prediction_approved(primary, min_confidence=self.settings.min_confidence, min_edge_bps=float(self.settings.min_edge_bps))
        model_approved = prediction_approved(model_result, min_confidence=self.settings.min_confidence, min_edge_bps=float(self.settings.min_edge_bps)) if model_result is not None else None
        alt_shadow_approved = prediction_approved(alt_shadow_result, min_confidence=self.settings.min_confidence, min_edge_bps=float(self.settings.min_edge_bps)) if alt_shadow_result is not None else None
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
            alt_shadow_result.confidence - primary.confidence
            if alt_shadow_result is not None
            else None
        )
        shadow_bucket = disagreement_bucket(
            fair_yes_delta=fair_yes_delta,
            selected_side_changed=(
                bool(model_result is not None and model_result.selected_side != primary.selected_side)
            ),
        )
        if self.settings.alt_shadow_enabled:
            alt_disagreement_bucket = disagreement_bucket(
                fair_yes_delta=alt_fair_yes_delta,
                selected_side_changed=(
                    bool(alt_shadow_result is not None and alt_shadow_result.selected_side != primary.selected_side)
                ),
            )
        else:
            alt_disagreement_bucket = "not_requested"
        parity_status = "ok" if not parity_warnings else "warning"
        self.last_parity_warnings = tuple(parity_warnings)
        self.last_shadow_comparison = {
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
                "min_confidence": self.settings.min_confidence,
                "min_edge_bps": self.settings.min_edge_bps,
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
        return primary.model_copy(update={"rationale": tuple(appended_rationale)})

    def _use_model_v2(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"model_v2", "artifact_model", "model", "model_v2_alt_promoted", "model_v2_alt"}

    def _use_shadow_mode(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"shadow", "shadow_scoring", "model_v2_shadow"}

    def _use_alt_promoted_mode(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"model_v2_alt_promoted", "model_v2_alt"}

    def _requires_runtime_model(self) -> bool:
        return self._use_model_v2() or self._use_shadow_mode() or self._use_alt_promoted_mode()

    def _model_confidence(
        self,
        *,
        fair_yes_prob: float,
        edge: float,
        candidate: MarketCandidate,
        features: dict[str, float],
    ) -> float:
        model_certainty = clamp_01(abs(fair_yes_prob - 0.5) * 2.0)
        edge_strength = clamp_01(abs(edge) / 0.25)
        disagreement = clamp_01(features.get("f_research_disagreement", 0.0))
        effective_cred = clamp_01(features.get("f_research_effective_credibility", 0.0))
        research_reliability = clamp_01(effective_cred * (1.0 - disagreement))
        value = (
            (model_certainty * 0.40)
            + (edge_strength * 0.25)
            + (research_reliability * 0.20)
            + (clamp_01(candidate.scan_score) * 0.15)
        )
        return clamp_01(value)
