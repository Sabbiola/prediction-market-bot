"""Property-based tests for the Kelly criterion implementation in RiskAgent."""
from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from prediction_market_bot.agents.risk import RiskAgent

# ── Strategies ───────────────────────────────────────────────────────────────
_prob = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
_price = st.floats(min_value=0.01, max_value=0.97, allow_nan=False, allow_infinity=False)


# ── Mathematical invariants ───────────────────────────────────────────────────


@given(fair_prob=_prob, market_price=_price)
def test_positive_edge_yields_positive_kelly(fair_prob: float, market_price: float) -> None:
    """When fair probability > market price, Kelly fraction must be strictly positive."""
    assume(fair_prob > market_price + 1e-6)
    result = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=market_price)
    assert result > 0.0, f"Expected positive Kelly for p={fair_prob}, c={market_price}, got {result}"


@given(fair_prob=_prob, market_price=_price)
def test_negative_edge_yields_nonpositive_kelly(fair_prob: float, market_price: float) -> None:
    """When fair probability < market price, Kelly fraction must be non-positive."""
    assume(fair_prob < market_price - 1e-6)
    result = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=market_price)
    assert result <= 0.0, f"Expected non-positive Kelly for p={fair_prob}, c={market_price}, got {result}"


@given(prob=_prob)
def test_zero_edge_yields_zero_kelly(prob: float) -> None:
    """When fair probability equals market price, Kelly fraction must be zero."""
    result = RiskAgent._kelly_fraction(fair_prob=prob, market_price=prob)
    assert abs(result) < 1e-9, f"Expected ~0 Kelly for p=c={prob}, got {result}"


@given(fair_prob=_prob, market_price=_price)
def test_kelly_never_exceeds_one(fair_prob: float, market_price: float) -> None:
    """Kelly fraction is bounded above by 1.0 for valid binary probabilities.

    Proof: f* = (p-c)/(1-c). Since p<=1, p-c<=1-c, so f* <= 1.
    """
    result = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=market_price)
    assert result <= 1.0 + 1e-9, f"Kelly exceeded 1.0: p={fair_prob}, c={market_price}, f={result}"


@given(
    p1=_prob,
    p2=_prob,
    market_price=_price,
)
def test_kelly_monotone_increasing_in_fair_prob(p1: float, p2: float, market_price: float) -> None:
    """Higher fair probability → higher Kelly fraction (monotone increasing in p)."""
    assume(abs(p1 - p2) > 1e-8)
    f1 = RiskAgent._kelly_fraction(fair_prob=p1, market_price=market_price)
    f2 = RiskAgent._kelly_fraction(fair_prob=p2, market_price=market_price)
    if p1 < p2:
        assert f1 <= f2 + 1e-9, f"Monotone violated: f({p1})={f1} > f({p2})={f2} at c={market_price}"
    else:
        assert f1 >= f2 - 1e-9, f"Monotone violated: f({p1})={f1} < f({p2})={f2} at c={market_price}"


@given(
    fair_prob=_prob,
    c1=_price,
    c2=_price,
)
def test_kelly_monotone_decreasing_in_market_price(fair_prob: float, c1: float, c2: float) -> None:
    """Higher market price → lower Kelly fraction (monotone decreasing in c).

    Proof: df*/dc = (p-1)/(1-c)^2 <= 0 since p <= 1.
    """
    assume(abs(c1 - c2) > 1e-8)
    f1 = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=c1)
    f2 = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=c2)
    if c1 < c2:
        assert f1 >= f2 - 1e-9, f"Monotone violated: higher c={c2} gave higher Kelly f={f2} vs c={c1} f={f1}"
    else:
        assert f1 <= f2 + 1e-9, f"Monotone violated: lower c={c2} gave lower Kelly f={f2} vs c={c1} f={f1}"


@given(fair_prob=_prob, market_price=_price)
def test_kelly_antisymmetric_sign(fair_prob: float, market_price: float) -> None:
    """Swapping fair_prob and market_price negates the sign of Kelly."""
    assume(abs(fair_prob - market_price) > 1e-6)
    f_forward = RiskAgent._kelly_fraction(fair_prob=fair_prob, market_price=market_price)
    # Not perfectly antisymmetric due to different denominators, but sign must flip
    f_reversed = RiskAgent._kelly_fraction(fair_prob=market_price, market_price=fair_prob)
    assert (f_forward > 0) != (f_reversed > 0) or (f_forward == 0 and f_reversed == 0), (
        f"Sign did not flip: f({fair_prob},{market_price})={f_forward}, f({market_price},{fair_prob})={f_reversed}"
    )
