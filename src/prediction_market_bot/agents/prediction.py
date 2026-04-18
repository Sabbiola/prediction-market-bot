from __future__ import annotations

from pathlib import Path
from typing import Any

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.agents.prediction_heuristic import (
    clamp_01,
    clamp_probability,
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
    build_runtime_prediction_features,
    enrich_with_btc_features,
)
from prediction_market_bot.agents.btc_feature_enricher import BtcFeatureEnricher, is_btc_updown_market
from prediction_market_bot.agents.prediction_shadow_engine import run_shadow_mode
from prediction_market_bot.agents.prediction_alt_promoted_engine import run_alt_promoted_mode
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket


def _parse_btc_interval(schema_version: str) -> str:
    """Extract candle interval from model schema version string.

    "btc-v2-15m" → "15m",  "btc-v2-5m" → "5m",  anything else → "5m".
    Ensures the live enricher fetches candles at the same resolution the model
    was trained on — critical for feature scale consistency.
    """
    for part in schema_version.split("-"):
        if part.endswith("m") and part[:-1].isdigit():
            return part
    return "5m"


class PredictionAgent:
    name = "prediction-agent"

    def __init__(
        self,
        settings: PredictionSettings,
        *,
        http_client: Any | None = None,
    ) -> None:
        self.settings = settings
        self._artifact_loader = PredictionModelArtifactLoader()
        self._runtime_model: RuntimePredictionModelContract | None = None
        self._model_load_error = ""
        self._alt_shadow_runtime_model: RuntimePredictionModelContract | None = None
        self._alt_shadow_model_load_error = ""
        self.last_shadow_comparison: dict[str, object] | None = None
        self.last_parity_warnings: tuple[str, ...] = ()
        self.last_runtime_features: dict[str, object] | None = None
        # Derive candle interval from model schema version so enricher resolution
        # matches training data resolution (e.g. "btc-v2-15m" → interval="15m").
        _btc_interval = _parse_btc_interval(settings.model_expected_feature_schema_version)
        # Inject the shared StructuredHttpClient (REC-05) so BTC external fetches
        # inherit allowed-hosts enforcement, retry/jitter and circuit breakers.
        self._btc_enricher: BtcFeatureEnricher = BtcFeatureEnricher(
            interval=_btc_interval,
            http_client=http_client,
        )
        self._load_runtime_model()

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        self.last_shadow_comparison = None
        self.last_parity_warnings = ()
        runtime_features = build_runtime_prediction_features(candidate, research)
        # Enrich with live BTC technicals when this is a BTC Up/Down market
        # Enrcih with live BTC technicals
        slug = str(getattr(candidate.market.market, "slug", "") or "").lower()
        title = str(getattr(candidate.market.market, "title", "") or "").lower()
        if is_btc_updown_market(slug, title):
            btc_feats = self._btc_enricher.get_features()
            if btc_feats:
                # Pass the expected schema version (e.g. "btc-v2-5m" or "btc-v2-15m")
                # so the runtime schema matches the promoted artifact — not hardcoded.
                expected_schema = self.settings.model_expected_feature_schema_version.strip()
                runtime_features = enrich_with_btc_features(
                    runtime_features,
                    btc_feats,
                    schema_version=expected_schema or "btc-v2-15m",
                )
        self.last_runtime_features = {
            "schema_version": runtime_features.schema_version,
            "decision_timestamp_utc": runtime_features.decision_timestamp_utc.isoformat(),
            "values": dict(runtime_features.values),
        }
        if self._use_shadow_mode():
            result = run_shadow_mode(
                settings=self.settings,
                runtime_model=self._runtime_model,
                model_load_error=self._model_load_error,
                alt_shadow_model=self._alt_shadow_runtime_model,
                alt_shadow_model_load_error=self._alt_shadow_model_load_error,
                candidate=candidate,
                research=research,
                runtime_features=runtime_features,
                predict_fn=self._predict_with_model_contract,
            )
            self.last_shadow_comparison = result.shadow_comparison
            self.last_parity_warnings = result.parity_warnings
            return result.prediction
        if self._use_alt_promoted_mode():
            result = run_alt_promoted_mode(
                settings=self.settings,
                runtime_model=self._runtime_model,
                model_load_error=self._model_load_error,
                alt_shadow_model=self._alt_shadow_runtime_model,
                alt_shadow_model_load_error=self._alt_shadow_model_load_error,
                candidate=candidate,
                research=research,
                runtime_features=runtime_features,
                predict_fn=self._predict_with_model_contract,
            )
            self.last_shadow_comparison = result.shadow_comparison or None
            self.last_parity_warnings = result.parity_warnings
            return result.prediction
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
