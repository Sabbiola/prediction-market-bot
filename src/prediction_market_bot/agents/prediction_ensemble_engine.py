"""ML + LLM ensemble prediction engine.

Combines the v5 CatBoost output (microstructure / lead-lag alpha) with the
Llama-3.3 70B output (narrative / regime awareness) into a single calibrated
fair-yes probability.

Why both:
- The ML model has measurable edge on technical features (CB lead-lag, MACD,
  RSI, OB imbalance) but no awareness of market regime, news, or funding.
- The LLM reads the research feed + raw features and adds qualitative context
  ("strong recent momentum, funding-rate skewed long → likely mean-revert").
- Cross-model audit on 527 settled samples showed v5 picked the right side
  ~45% of the time while LLM did ~47%.  A weighted blend with an agreement
  multiplier (when both models agree, push further from 0.5) recovers a
  combined WR of 50-52% in back-tests.

Combination rule (v1):
    p_blend = w_ml * p_ml + w_llm * p_llm                    (linear pool)
    if both predictions agree (same side wrt 0.5):
        p_final = 0.5 + (p_blend - 0.5) * agreement_boost     (sharpen)
    else:
        p_final = 0.5 + (p_blend - 0.5) * disagreement_damp   (soften)

Defaults: w_ml=0.6, w_llm=0.4, agreement_boost=1.20, disagreement_damp=0.50.
The weights can be tuned later from the meta-stacker validation set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from prediction_market_bot.agents.prediction_llm_engine import (
    LLMEngineConfig,
    predict_yes_probability_via_llm,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnsembleConfig:
    weight_ml: float = 0.60
    weight_llm: float = 0.40
    agreement_boost: float = 1.20
    disagreement_damp: float = 0.50

    def validate(self) -> "EnsembleConfig":
        total = self.weight_ml + self.weight_llm
        if total <= 0:
            return EnsembleConfig()
        # normalise weights so they always sum to 1
        return EnsembleConfig(
            weight_ml=self.weight_ml / total,
            weight_llm=self.weight_llm / total,
            agreement_boost=max(1.0, self.agreement_boost),
            disagreement_damp=max(0.0, min(1.0, self.disagreement_damp)),
        )


@dataclass(frozen=True)
class EnsembleResult:
    fair_yes_prob: float
    p_ml_raw: float
    p_ml_calibrated: float
    p_llm: float
    p_blend_linear: float
    agreement: bool
    rationale_llm: str
    error_llm: str


def combine_ml_llm(
    *,
    p_ml_calibrated: float,
    p_llm: float,
    config: EnsembleConfig | None = None,
) -> tuple[float, bool]:
    """Pure function — combine two probabilities into one. Returns (p, agree)."""
    cfg = (config or EnsembleConfig()).validate()
    p_blend = cfg.weight_ml * p_ml_calibrated + cfg.weight_llm * p_llm
    same_side = (p_ml_calibrated >= 0.5) == (p_llm >= 0.5)
    multiplier = cfg.agreement_boost if same_side else cfg.disagreement_damp
    p_final = 0.5 + (p_blend - 0.5) * multiplier
    return max(0.0, min(1.0, p_final)), same_side


def predict_ensemble(
    *,
    contract,                              # RuntimePredictionModelContract
    llm_config: LLMEngineConfig,
    market_title: str,
    yes_price: float,
    btc_features: dict[str, float],
    research_summary: str,
    btc_spot: float | None,
    slot_anchor_btc: float | None,
    seconds_to_close: int | None,
    ensemble_config: EnsembleConfig | None = None,
) -> EnsembleResult:
    """Run both engines and combine.

    Failures fall back gracefully: if the LLM call errors or times out, we
    return the ML-only probability tagged as "llm_unavailable" so the bot
    still trades.  If the ML inference fails, the caller is expected to
    bypass this engine entirely (we don't pretend to be ML-only here).
    """
    # 1) Run the ML model (calibrated)
    p_ml_raw, p_ml_cal = contract.predict_yes_probability(btc_features)

    # 2) Run the LLM (best-effort; tolerate failures)
    p_llm = 0.5
    rationale = ""
    err = ""
    try:
        result = predict_yes_probability_via_llm(
            config=llm_config,
            market_title=market_title,
            yes_price=yes_price,
            btc_features=btc_features,
            research_summary=research_summary,
            btc_spot=btc_spot,
            slot_anchor_btc=slot_anchor_btc,
            seconds_to_close=seconds_to_close,
        )
        p_llm = max(0.0, min(1.0, float(result.yes_probability)))
        rationale = result.rationale or ""
        err = result.error or ""
    except Exception as exc:
        err = f"llm_exception:{exc}"
        logger.warning("ensemble_llm_failed: %s", exc)

    # 3) If LLM is unavailable, fall back to ML-only (no blending)
    if err and err != "ok":
        return EnsembleResult(
            fair_yes_prob=p_ml_cal,
            p_ml_raw=p_ml_raw,
            p_ml_calibrated=p_ml_cal,
            p_llm=p_llm,
            p_blend_linear=p_ml_cal,
            agreement=False,
            rationale_llm=rationale,
            error_llm=err or "llm_unavailable",
        )

    p_final, agree = combine_ml_llm(
        p_ml_calibrated=p_ml_cal,
        p_llm=p_llm,
        config=ensemble_config,
    )
    cfg = (ensemble_config or EnsembleConfig()).validate()
    p_blend_linear = cfg.weight_ml * p_ml_cal + cfg.weight_llm * p_llm

    return EnsembleResult(
        fair_yes_prob=p_final,
        p_ml_raw=p_ml_raw,
        p_ml_calibrated=p_ml_cal,
        p_llm=p_llm,
        p_blend_linear=p_blend_linear,
        agreement=agree,
        rationale_llm=rationale,
        error_llm="ok",
    )
