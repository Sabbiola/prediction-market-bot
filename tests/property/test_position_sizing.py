"""Property-based tests for RiskAgent position sizing invariants."""
from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import PredictionResult

# ── Strategies ───────────────────────────────────────────────────────────────
_bankroll = st.floats(min_value=1_000.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_pct = st.floats(min_value=0.005, max_value=0.20, allow_nan=False, allow_infinity=False)
_prob = st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False)


def _make_prediction(market_price: float, fair_price: float) -> PredictionResult:
    edge = fair_price - market_price
    return PredictionResult(
        market_id="m-size",
        selected_side=OutcomeSide.YES,
        market_yes_prob=round(market_price, 4),
        fair_yes_prob=round(fair_price, 4),
        selected_market_price=round(market_price, 4),
        selected_fair_price=round(fair_price, 4),
        edge=round(edge, 4),
        confidence=0.95,
        rationale=("property-test",),
    )


# ── Stake is always non-negative ──────────────────────────────────────────────


@given(
    bankroll=_bankroll,
    max_pos_pct=_pct,
    market_price=_prob,
    fair_price=_prob,
)
@settings(max_examples=300)
def test_stake_always_nonnegative(
    bankroll: float, max_pos_pct: float, market_price: float, fair_price: float
) -> None:
    """Approved stake must never be negative."""
    risk_s = RiskSettings(
        bankroll_usd=bankroll,
        max_position_pct=max_pos_pct,
        max_event_bucket_pct=max_pos_pct * 4,
        max_category_bucket_pct=max_pos_pct * 5,
        max_portfolio_exposure_pct=max_pos_pct * 5,
        max_per_market_exposure_pct=max_pos_pct,
        min_liquidity_usd=0.0,
        max_spread_bps=10_000,
        max_snapshot_age_sec=999_999,
        min_bet_usd=0.01,
        daily_stop_loss_pct=0.99,
    )
    pred_s = PredictionSettings(min_confidence=0.0, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price)
    decision = agent.run(pred)
    assert decision.stake_usd >= 0.0, f"stake_usd={decision.stake_usd} is negative"


# ── Approved stake bounded by position cap ────────────────────────────────────


@given(
    bankroll=_bankroll,
    max_pos_pct=_pct,
    market_price=st.floats(min_value=0.05, max_value=0.50, allow_nan=False, allow_infinity=False),
    fair_price=st.floats(min_value=0.60, max_value=0.95, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_approved_stake_bounded_by_position_cap(
    bankroll: float, max_pos_pct: float, market_price: float, fair_price: float
) -> None:
    """Approved stake must not exceed bankroll × max_position_pct."""
    assume(fair_price > market_price + 0.05)
    risk_s = RiskSettings(
        bankroll_usd=bankroll,
        fractional_kelly=0.50,  # higher fraction to trigger cap
        max_position_pct=max_pos_pct,
        max_event_bucket_pct=max_pos_pct * 10,
        max_category_bucket_pct=max_pos_pct * 10,
        max_portfolio_exposure_pct=max_pos_pct * 10,
        max_per_market_exposure_pct=max_pos_pct * 10,
        min_liquidity_usd=0.0,
        max_spread_bps=10_000,
        max_snapshot_age_sec=999_999,
        min_bet_usd=0.01,
        daily_stop_loss_pct=0.99,
    )
    pred_s = PredictionSettings(min_confidence=0.0, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price)
    decision = agent.run(pred)
    cap_usd = bankroll * max_pos_pct
    # Allow 0.01 USD tolerance: RiskAgent rounds stake_usd to 2 decimal
    # places which can add up to 0.005 above the theoretical cap.
    assert decision.stake_usd <= cap_usd + 0.01, (
        f"stake_usd={decision.stake_usd:.4f} exceeds cap={cap_usd:.4f} "
        f"(bankroll={bankroll}, max_pos_pct={max_pos_pct})"
    )


# ── Approved stake respects min_bet_usd ──────────────────────────────────────


@given(
    bankroll=_bankroll,
    min_bet=st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False),
    market_price=st.floats(min_value=0.05, max_value=0.50, allow_nan=False, allow_infinity=False),
    fair_price=st.floats(min_value=0.60, max_value=0.95, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=300)
def test_approved_stake_at_least_min_bet(
    bankroll: float, min_bet: float, market_price: float, fair_price: float
) -> None:
    """When a trade is approved, stake_usd must be >= min_bet_usd."""
    assume(fair_price > market_price + 0.05)
    risk_s = RiskSettings(
        bankroll_usd=bankroll,
        fractional_kelly=1.0,
        max_position_pct=0.20,
        max_event_bucket_pct=0.50,
        max_category_bucket_pct=0.50,
        max_portfolio_exposure_pct=0.50,
        max_per_market_exposure_pct=0.50,
        min_liquidity_usd=0.0,
        max_spread_bps=10_000,
        max_snapshot_age_sec=999_999,
        min_bet_usd=min_bet,
        daily_stop_loss_pct=0.99,
    )
    pred_s = PredictionSettings(min_confidence=0.0, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price)
    decision = agent.run(pred)
    if decision.approved:
        assert decision.stake_usd >= min_bet - 1e-4, (
            f"Approved stake {decision.stake_usd:.4f} < min_bet {min_bet:.4f}"
        )


# ── Bankroll fraction is bounded ──────────────────────────────────────────────


@given(
    bankroll=_bankroll,
    max_pos_pct=_pct,
    market_price=_prob,
    fair_price=_prob,
)
@settings(max_examples=300)
def test_bankroll_fraction_bounded_by_caps(
    bankroll: float, max_pos_pct: float, market_price: float, fair_price: float
) -> None:
    """bankroll_fraction in the RiskDecision must not exceed the position cap."""
    cap = min(max_pos_pct, 0.20)  # minimum of all caps
    risk_s = RiskSettings(
        bankroll_usd=bankroll,
        fractional_kelly=1.0,
        max_position_pct=cap,
        max_event_bucket_pct=cap,
        max_category_bucket_pct=cap,
        max_portfolio_exposure_pct=cap,
        max_per_market_exposure_pct=cap,
        min_liquidity_usd=0.0,
        max_spread_bps=10_000,
        max_snapshot_age_sec=999_999,
        min_bet_usd=0.01,
        daily_stop_loss_pct=0.99,
    )
    pred_s = PredictionSettings(min_confidence=0.0, min_edge_bps=0)
    agent = RiskAgent(risk_s, pred_s)
    pred = _make_prediction(market_price=market_price, fair_price=fair_price)
    decision = agent.run(pred)
    assert decision.bankroll_fraction <= cap + 1e-6, (
        f"bankroll_fraction={decision.bankroll_fraction:.6f} exceeds cap={cap:.6f}"
    )
