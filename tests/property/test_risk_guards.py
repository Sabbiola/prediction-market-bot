"""Property-based tests for RiskAgent guard conditions (circuit breakers, thresholds)."""
from __future__ import annotations

import dataclasses

from hypothesis import given, settings
from hypothesis import strategies as st

from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import PredictionResult

# ── Strategies ───────────────────────────────────────────────────────────────
_prob = st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False)
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_edge = st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False)


def _make_prediction(
    market_price: float = 0.40,
    fair_price: float = 0.55,
    confidence: float = 0.85,
    market_id: str = "m-prop",
) -> PredictionResult:
    edge = fair_price - market_price
    return PredictionResult(
        market_id=market_id,
        selected_side=OutcomeSide.YES,
        market_yes_prob=round(market_price, 4),
        fair_yes_prob=round(fair_price, 4),
        selected_market_price=round(market_price, 4),
        selected_fair_price=round(fair_price, 4),
        edge=round(edge, 4),
        confidence=round(confidence, 4),
        rationale=("property-test",),
    )


def _default_settings() -> tuple[RiskSettings, PredictionSettings]:
    return (
        RiskSettings(
            bankroll_usd=10_000.0,
            fractional_kelly=0.25,
            max_position_pct=0.05,
            max_event_bucket_pct=0.10,
            max_category_bucket_pct=0.15,
            max_portfolio_exposure_pct=0.20,
            max_per_market_exposure_pct=0.05,
            min_liquidity_usd=0.0,  # disable liquidity guard
            max_spread_bps=10_000,  # disable spread guard
            max_snapshot_age_sec=999_999,  # disable stale guard
            min_bet_usd=1.0,
            daily_stop_loss_pct=0.99,
        ),
        PredictionSettings(min_confidence=0.5, min_edge_bps=100),
    )


# ── Circuit breaker is absolute ───────────────────────────────────────────────


@given(
    market_price=_prob,
    fair_price=_prob,
    confidence=_confidence,
)
@settings(max_examples=200)
def test_manual_pause_always_blocks(
    market_price: float, fair_price: float, confidence: float
) -> None:
    """manual_pause=True must block the trade unconditionally."""
    risk_s, pred_s = _default_settings()
    risk_s = dataclasses.replace(risk_s, manual_pause=True)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price, confidence=confidence)
    decision = agent.run(pred)
    assert not decision.approved, "manual_pause should block all trades"
    assert decision.stake_usd == 0.0


@given(
    market_price=_prob,
    fair_price=_prob,
    confidence=_confidence,
)
@settings(max_examples=200)
def test_global_circuit_breaker_always_blocks(
    market_price: float, fair_price: float, confidence: float
) -> None:
    """global_circuit_breaker=True must block unconditionally."""
    risk_s, pred_s = _default_settings()
    risk_s = dataclasses.replace(risk_s, global_circuit_breaker=True)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price, confidence=confidence)
    decision = agent.run(pred)
    assert not decision.approved, "global_circuit_breaker should block all trades"
    assert decision.stake_usd == 0.0


# ── Confidence threshold ──────────────────────────────────────────────────────


@given(
    confidence=st.floats(min_value=0.0, max_value=0.899, allow_nan=False, allow_infinity=False),
    market_price=_prob,
    fair_price=_prob,
)
@settings(max_examples=200)
def test_low_confidence_blocks_trade(
    confidence: float, market_price: float, fair_price: float
) -> None:
    """Confidence below min_confidence must block the trade."""
    min_conf = 0.90
    risk_s, _ = _default_settings()
    pred_s = PredictionSettings(min_confidence=min_conf, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price, confidence=confidence)
    decision = agent.run(pred)
    assert not decision.approved, (
        f"confidence={confidence} < min={min_conf} should block, but was approved"
    )


@given(
    confidence=st.floats(min_value=0.901, max_value=1.0, allow_nan=False, allow_infinity=False),
    market_price=st.floats(min_value=0.05, max_value=0.55, allow_nan=False, allow_infinity=False),
    fair_price=st.floats(min_value=0.65, max_value=0.95, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_sufficient_confidence_does_not_block_on_confidence(
    confidence: float, market_price: float, fair_price: float
) -> None:
    """Confidence above min_confidence should not trigger a confidence block reason."""
    min_conf = 0.90
    risk_s, _ = _default_settings()
    pred_s = PredictionSettings(min_confidence=min_conf, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price, confidence=confidence)
    decision = agent.run(pred)
    reasons = " ".join(decision.reasoning)
    assert "confidence_below_threshold" not in reasons, (
        f"confidence={confidence} >= min={min_conf} triggered confidence block: {reasons}"
    )


# ── Approved stake is always zero when blocked ────────────────────────────────


@given(
    market_price=_prob,
    fair_price=_prob,
    confidence=_confidence,
)
@settings(max_examples=300)
def test_not_approved_means_zero_stake(
    market_price: float, fair_price: float, confidence: float
) -> None:
    """When a trade is not approved, returned stake_usd must be 0."""
    risk_s, pred_s = _default_settings()
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price, confidence=confidence)
    decision = agent.run(pred)
    if not decision.approved:
        assert decision.stake_usd == 0.0, (
            f"Blocked trade has non-zero stake: {decision.stake_usd}"
        )
        assert decision.bankroll_fraction == 0.0
