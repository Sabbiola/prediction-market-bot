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
    enrich_with_slot_features,
)
from prediction_market_bot.agents.btc_feature_enricher import BtcFeatureEnricher, is_btc_updown_market
from prediction_market_bot.agents.prediction_shadow_engine import run_shadow_mode
from prediction_market_bot.agents.prediction_alt_promoted_engine import run_alt_promoted_mode
from prediction_market_bot.agents.regime_gate import RegimeGateConfig, evaluate_regime_gate
from prediction_market_bot.agents.prediction_llm_engine import (
    LLMEngineConfig,
    predict_yes_probability_via_llm,
)
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
            # Slot-specific latency arb features: elapsed fraction, BTC vs anchor, Polymarket misprice.
            # Safe to call even when btc_feats is empty; degrades gracefully.
            slot_feats = self._btc_enricher.get_slot_features(
                hours_to_resolution=float(candidate.market.hours_to_resolution),
                market_updated_at=candidate.market.market.updated_at,
                yes_price=float(candidate.market.yes_price),
            )
            runtime_features = enrich_with_slot_features(runtime_features, slot_feats)
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
        if self._use_llm_only():
            return self._run_llm_only(candidate, research, runtime_features=runtime_features)
        if self._use_ensemble():
            return self._run_ensemble(candidate, research, runtime_features=runtime_features)
        if self._use_rsi_ml():
            return self._run_rsi_ml(candidate, research, runtime_features=runtime_features)
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

        # ── Regime gate: empirically-derived veto for known-loss patterns.
        # When a bad regime is detected we collapse fair_yes_prob to the
        # current market price so the downstream edge becomes 0 and the
        # risk agent declines the trade.  Decision is recorded in rationale.
        regime_decision = self._evaluate_regime_gate(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            features=features.values,
        )
        if not regime_decision.allowed:
            fair_yes_prob = float(candidate.market.yes_price)

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
            f"regime_gate={regime_decision.reason}",
        )
        prediction = prediction_from_fair_probability(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            confidence=confidence,
            rationale=rationale,
        )
        return prediction, parity_tuple, raw_yes_prob

    def _evaluate_regime_gate(
        self,
        *,
        candidate: MarketCandidate,
        fair_yes_prob: float,
        features: dict[str, float],
    ):
        """Apply the empirical regime gate to a model prediction.

        Returns a RegimeGateDecision (always — even when disabled, so callers
        can record the reason in the audit trail).
        """
        config = RegimeGateConfig(
            enabled=self.settings.regime_gate_enabled,
            trend_follow_block_threshold=self.settings.regime_gate_trend_follow_threshold,
            fill_price_skip_min=self.settings.regime_gate_fill_price_skip_min,
            fill_price_skip_max=self.settings.regime_gate_fill_price_skip_max,
            skip_extreme_rsi=self.settings.regime_gate_skip_extreme_rsi,
            skip_bad_hours_utc=self.settings.regime_gate_skip_bad_hours_utc,
        )
        # Inferred side: we'd take the side the model favours.
        side = "YES" if fair_yes_prob >= float(candidate.market.yes_price) else "NO"
        # Hour from the runtime decision time, captured in the runtime features.
        hour_utc: int | None = None
        if self.last_runtime_features:
            try:
                from datetime import datetime as _dt
                ts = str(self.last_runtime_features.get("decision_timestamp_utc") or "")
                if ts:
                    hour_utc = _dt.fromisoformat(ts.replace("Z", "+00:00")).hour
            except Exception:
                hour_utc = None
        return evaluate_regime_gate(
            config=config,
            fair_yes_prob=fair_yes_prob,
            candidate_side=side,
            yes_price=float(candidate.market.yes_price),
            features=features,
            decision_hour_utc=hour_utc,
        )

    def _use_model_v2(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"model_v2", "artifact_model", "model", "model_v2_alt_promoted", "model_v2_alt"}

    def _use_llm_only(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"llm_only", "llm", "llm_engine"}

    def _use_ensemble(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"ensemble", "ml_llm_ensemble", "blend"}

    def _use_rsi_ml(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"rsi_ml", "rsi_revert", "mean_reversion_rsi_ml"}

    def _run_llm_only(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        """LLM-only prediction. Sends BTC features + research to a chat model
        and parses a calibrated probability. Independent of CatBoost/regime gate.
        """
        cfg = LLMEngineConfig(
            api_key_env=self.settings.llm_api_key_env,
            endpoint_url=self.settings.llm_endpoint_url,
            model=self.settings.llm_model,
            temperature=self.settings.llm_temperature,
            timeout_sec=self.settings.llm_timeout_sec,
            max_retries=self.settings.llm_max_retries,
        )
        title = str(getattr(candidate.market.market, "title", "") or "")
        yes_price = float(candidate.market.yes_price)
        feats = dict(runtime_features.values)
        # Compose a brief research summary so the LLM has narrative context too.
        research_lines: list[str] = []
        try:
            for finding in (research.findings or ())[:5]:
                src = getattr(finding, "source", "") or ""
                summary = getattr(finding, "summary", "") or ""
                cred = getattr(finding, "credibility", 0.0)
                if summary:
                    research_lines.append(f"  [{src}, cred={cred:.2f}] {summary[:160]}")
        except Exception:
            pass
        research_summary = "\n".join(research_lines)
        slot_anchor = feats.get("f_slot_anchor_btc_usd")
        btc_spot = feats.get("f_btc_spot_usd")
        secs_to_close = feats.get("f_seconds_to_slot_close")

        result = predict_yes_probability_via_llm(
            config=cfg,
            market_title=title,
            yes_price=yes_price,
            btc_features=feats,
            research_summary=research_summary,
            btc_spot=float(btc_spot) if btc_spot is not None else None,
            slot_anchor_btc=float(slot_anchor) if slot_anchor is not None else None,
            seconds_to_close=int(secs_to_close) if secs_to_close is not None else None,
        )
        fair_yes_prob = clamp_probability(result.yes_probability)
        confidence = self._model_confidence(
            fair_yes_prob=fair_yes_prob,
            edge=edge_for_side(candidate=candidate, fair_yes_prob=fair_yes_prob),
            candidate=candidate,
            features=feats,
        )
        rationale = (
            "prediction_engine=llm_only",
            f"llm_provider={self.settings.llm_provider}",
            f"llm_model={self.settings.llm_model}",
            f"llm_yes_prob={fair_yes_prob:.4f}",
            f"confidence={confidence:.4f}",
            f"llm_error={result.error or 'ok'}",
            f"llm_rationale={(result.rationale or '')[:200]}",
        )
        return prediction_from_fair_probability(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            confidence=confidence,
            rationale=rationale,
        )

    def _run_ensemble(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        """ML + LLM blended prediction.

        Falls back to heuristic if the ML artifact failed to load.  The LLM
        is best-effort: a transient API failure degrades to ML-only without
        skipping the trade.
        """
        from prediction_market_bot.agents.prediction_ensemble_engine import (
            EnsembleConfig,
            predict_ensemble,
        )

        if self._runtime_model is None:
            base = run_heuristic(self.settings, candidate, research)
            reason = self._model_load_error or "ensemble_no_runtime_model"
            return PredictionResult(
                **{
                    **base.__dict__,
                    "rationale": tuple((*base.rationale, f"prediction_engine_fallback=heuristic reason={reason}")),
                }
            )

        cfg_llm = LLMEngineConfig(
            api_key_env=self.settings.llm_api_key_env,
            endpoint_url=self.settings.llm_endpoint_url,
            model=self.settings.llm_model,
            temperature=self.settings.llm_temperature,
            timeout_sec=self.settings.llm_timeout_sec,
            max_retries=self.settings.llm_max_retries,
        )
        cfg_ens = EnsembleConfig(
            weight_ml=self.settings.ensemble_weight_ml,
            weight_llm=self.settings.ensemble_weight_llm,
            agreement_boost=self.settings.ensemble_agreement_boost,
            disagreement_damp=self.settings.ensemble_disagreement_damp,
        )

        title = str(getattr(candidate.market.market, "title", "") or "")
        yes_price = float(candidate.market.yes_price)
        feats = dict(runtime_features.values)
        research_lines: list[str] = []
        try:
            for finding in (research.findings or ())[:5]:
                src = getattr(finding, "source", "") or ""
                summary = getattr(finding, "summary", "") or ""
                cred = getattr(finding, "credibility", 0.0)
                if summary:
                    research_lines.append(f"  [{src}, cred={cred:.2f}] {summary[:160]}")
        except Exception:
            pass
        research_summary = "\n".join(research_lines)
        slot_anchor = feats.get("f_slot_anchor_btc_usd")
        btc_spot = feats.get("f_btc_spot_usd")
        secs_to_close = feats.get("f_seconds_to_slot_close")

        result = predict_ensemble(
            contract=self._runtime_model,
            llm_config=cfg_llm,
            market_title=title,
            yes_price=yes_price,
            btc_features=feats,
            research_summary=research_summary,
            btc_spot=float(btc_spot) if btc_spot is not None else None,
            slot_anchor_btc=float(slot_anchor) if slot_anchor is not None else None,
            seconds_to_close=int(secs_to_close) if secs_to_close is not None else None,
            ensemble_config=cfg_ens,
        )

        fair_yes_prob = clamp_probability(result.fair_yes_prob)
        confidence = self._model_confidence(
            fair_yes_prob=fair_yes_prob,
            edge=edge_for_side(candidate=candidate, fair_yes_prob=fair_yes_prob),
            candidate=candidate,
            features=feats,
        )
        rationale = (
            "prediction_engine=ensemble",
            f"p_ml_raw={result.p_ml_raw:.4f}",
            f"p_ml_calibrated={result.p_ml_calibrated:.4f}",
            f"p_llm={result.p_llm:.4f}",
            f"p_blend_linear={result.p_blend_linear:.4f}",
            f"p_final={fair_yes_prob:.4f}",
            f"agreement={result.agreement}",
            f"weights_ml/llm={cfg_ens.weight_ml:.2f}/{cfg_ens.weight_llm:.2f}",
            f"llm_status={result.error_llm}",
            f"llm_rationale={(result.rationale_llm or '')[:160]}",
            f"confidence={confidence:.4f}",
        )
        return prediction_from_fair_probability(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            confidence=confidence,
            rationale=rationale,
        )

    def _run_rsi_ml(
        self,
        candidate: MarketCandidate,
        research: ResearchPacket,
        *,
        runtime_features: RuntimePredictionFeatures,
    ) -> PredictionResult:
        """Mean-reversion RSI + ML filter engine (Bot G).

        The backtest-validated profitable strategy: RSI extremes generate
        the entry signal, the v7 TP-classifier (loaded as the runtime
        model) acts as a quality filter, and the executor manages exit
        with TP/SL/time-exit on HL.
        """
        from prediction_market_bot.agents.prediction_rsi_ml_engine import (
            RsiMLConfig,
            predict_rsi_ml,
        )

        feats = dict(runtime_features.values)
        # The btc enricher caches recent close prices; use 1h closes if
        # available, else fall back to whatever close-history feature
        # vector the enricher exposes.  Conservative: use feats sweep.
        closes = []
        for k in sorted(feats.keys()):
            if k.startswith("f_btc_close_h_"):
                try:
                    closes.append(float(feats[k]))
                except Exception:
                    continue
        if not closes:
            # No 1h close history: fall back to neutral (skip)
            base = run_heuristic(self.settings, candidate, research)
            return PredictionResult(
                **{
                    **base.__dict__,
                    "fair_yes_prob": 0.5,
                    "rationale": tuple((
                        *base.rationale,
                        "prediction_engine=rsi_ml",
                        "rsi_ml_neutral_no_close_history",
                    )),
                }
            )

        # ML filter probabilities: when the ML artefact is loaded we use
        # its calibrated yes-prob as a proxy for P(TP_hit_long); the
        # mirror (1 - p) is the SHORT side baseline.  v7 (two classifiers)
        # support is added later; for now reuse v6/v5 contract if present.
        p_long_ml = None
        p_short_ml = None
        if self._runtime_model is not None:
            try:
                _raw, p_cal = self._runtime_model.predict_yes_probability(feats)
                p_long_ml  = float(p_cal)
                p_short_ml = 1.0 - p_long_ml
            except Exception as exc:
                logger.debug("rsi_ml_model_predict_failed err=%s", exc)

        cfg = RsiMLConfig(
            rsi_low=self.settings.rsi_ml_rsi_low,
            rsi_high=self.settings.rsi_ml_rsi_high,
            ml_min_prob=self.settings.rsi_ml_min_prob,
            edge_strength=self.settings.rsi_ml_edge_strength,
        )
        prob, side, dbg = predict_rsi_ml(
            config=cfg,
            hourly_closes=closes,
            p_long_ml=p_long_ml,
            p_short_ml=p_short_ml,
        )
        fair_yes_prob = clamp_probability(prob)
        edge = edge_for_side(candidate=candidate, fair_yes_prob=fair_yes_prob)
        confidence = self._model_confidence(
            fair_yes_prob=fair_yes_prob, edge=edge,
            candidate=candidate, features=feats,
        )
        rationale = (
            "prediction_engine=rsi_ml",
            f"rsi={dbg.get('rsi','-'):.2f}" if isinstance(dbg.get("rsi"), float) else "rsi=-",
            f"side={side}",
            f"p_long_ml={p_long_ml}",
            f"p_short_ml={p_short_ml}",
            f"reason={dbg.get('reason','')}",
            f"fair_yes_prob={fair_yes_prob:.4f}",
            f"confidence={confidence:.4f}",
        )
        return prediction_from_fair_probability(
            candidate=candidate,
            fair_yes_prob=fair_yes_prob,
            confidence=confidence,
            rationale=rationale,
        )

    def _use_shadow_mode(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"shadow", "shadow_scoring", "model_v2_shadow"}

    def _use_alt_promoted_mode(self) -> bool:
        mode = self.settings.engine.strip().lower()
        return mode in {"model_v2_alt_promoted", "model_v2_alt"}

    def _requires_runtime_model(self) -> bool:
        # llm_only doesn't need a CatBoost artifact loaded.
        # ensemble does — it blends ML probability with LLM probability.
        # rsi_ml uses the ML probability only as a filter, but loads it
        # the same way for parity with the live engine config.
        return (
            self._use_model_v2()
            or self._use_shadow_mode()
            or self._use_alt_promoted_mode()
            or self._use_ensemble()
            or self._use_rsi_ml()
        )

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
