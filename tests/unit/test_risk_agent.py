from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult


@dataclass(slots=True, frozen=True)
class _PortfolioView:
    total_exposure_usd: float
    realized_pnl_usd: float
    market_exposure_usd: dict[str, float]


def _prediction(
    *,
    market_id: str = "m-risk",
    side: OutcomeSide = OutcomeSide.YES,
    market_price: float = 0.40,
    fair_price: float = 0.55,
    confidence: float = 0.80,
) -> PredictionResult:
    edge = fair_price - market_price
    fair_yes_prob = fair_price if side is OutcomeSide.YES else 1.0 - fair_price
    market_yes_prob = market_price if side is OutcomeSide.YES else 1.0 - market_price
    return PredictionResult(
        market_id=market_id,
        selected_side=side,
        market_yes_prob=round(market_yes_prob, 4),
        fair_yes_prob=round(fair_yes_prob, 4),
        selected_market_price=round(market_price, 4),
        selected_fair_price=round(fair_price, 4),
        edge=round(edge, 4),
        confidence=round(confidence, 4),
        rationale=("test",),
    )


def _candidate(
    *,
    market_id: str = "m-risk",
    liquidity_usd: float = 50_000.0,
    spread_bps: int = 120,
    updated_at: datetime | None = None,
) -> MarketCandidate:
    snapshot = MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue="polymarket",
        title="risk test market",
        yes_price=0.4,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=25_000.0,
        spread_bps=spread_bps,
        hours_to_resolution=24.0,
        last_price_move_bps=10,
        updated_at=updated_at,
    )
    return MarketCandidate(market=snapshot, scan_score=0.8, reasons=("risk-test",))


def _risk_settings(**overrides: float | int | bool) -> RiskSettings:
    payload: dict[str, float | int | bool] = {
        "bankroll_usd": 10_000.0,
        "fractional_kelly": 0.25,
        "max_position_pct": 0.02,
        "max_event_bucket_pct": 0.08,
        "max_category_bucket_pct": 0.10,
        "max_portfolio_exposure_pct": 0.10,
        "max_per_market_exposure_pct": 0.02,
        "max_daily_loss_pct": 0.05,
        "daily_stop_loss_pct": 0.05,
        "min_liquidity_usd": 10_000.0,
        "max_spread_bps": 300,
        "max_snapshot_age_sec": 900,
        "global_circuit_breaker": False,
        "manual_pause": False,
        "min_bet_usd": 25.0,
    }
    payload.update(overrides)
    return RiskSettings(**payload)


def _prediction_settings(**overrides: float | int) -> PredictionSettings:
    return PredictionSettings(
        min_confidence=0.62,
        min_edge_bps=300,
        **overrides,
    )


def _agent(now: datetime, **risk_overrides: float | int | bool) -> RiskAgent:
    return RiskAgent(
        _risk_settings(**risk_overrides),
        _prediction_settings(),
        now_fn=lambda: now,
    )


def _assert_rejected_with_reason(decision_reasoning: tuple[str, ...], reason_prefix: str) -> None:
    assert len(decision_reasoning) > 0
    assert any(item.startswith(reason_prefix) for item in decision_reasoning)


def test_risk_agent_approves_when_all_guardrails_pass() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    candidate = _candidate(updated_at=now - timedelta(seconds=60))
    portfolio = _PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={})

    decision = agent.run(_prediction(), candidate=candidate, portfolio=portfolio)
    assert decision.approved is True
    assert decision.stake_usd == 200.0
    assert "approved" in decision.reasoning


def test_risk_agent_blocks_on_minimum_confidence_threshold() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    decision = agent.run(
        _prediction(confidence=0.40),
        candidate=_candidate(updated_at=now),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    assert decision.stake_usd == 0.0
    _assert_rejected_with_reason(decision.reasoning, "confidence_below_threshold")


def test_risk_agent_blocks_on_minimum_edge_threshold() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    decision = agent.run(
        _prediction(market_price=0.50, fair_price=0.51, confidence=0.95),
        candidate=_candidate(updated_at=now),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "edge_below_threshold")


def test_risk_agent_blocks_on_minimum_liquidity_threshold() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now, liquidity_usd=1_000.0),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "liquidity_below_threshold")


def test_risk_agent_blocks_on_maximum_spread_threshold() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now, spread_bps=500),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "spread_above_threshold")


def test_risk_agent_blocks_on_stale_data() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now, max_snapshot_age_sec=300)
    candidate = _candidate(updated_at=now - timedelta(seconds=1_000))
    decision = agent.run(
        _prediction(),
        candidate=candidate,
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "stale_market_data")


def test_risk_agent_blocks_on_max_portfolio_exposure() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now, max_portfolio_exposure_pct=0.10)
    portfolio = _PortfolioView(total_exposure_usd=900.0, realized_pnl_usd=0.0, market_exposure_usd={})
    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now),
        portfolio=portfolio,
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "portfolio_exposure_cap_reached")


def test_risk_agent_blocks_on_max_per_market_exposure() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now, max_per_market_exposure_pct=0.02)
    portfolio = _PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={"m-risk": 150.0})
    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now),
        portfolio=portfolio,
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "market_exposure_cap_reached")


def test_risk_agent_triggers_daily_stop_and_global_circuit_breaker() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now, daily_stop_loss_pct=0.05)
    portfolio = _PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=-600.0, market_exposure_usd={})

    first = agent.run(_prediction(), candidate=_candidate(updated_at=now), portfolio=portfolio)
    assert first.approved is False
    _assert_rejected_with_reason(first.reasoning, "daily_stop_triggered")
    _assert_rejected_with_reason(first.reasoning, "global_circuit_breaker_trip_daily_stop")

    second = agent.run(_prediction(), candidate=_candidate(updated_at=now), portfolio=portfolio)
    assert second.approved is False
    _assert_rejected_with_reason(second.reasoning, "global_circuit_breaker_active")


def test_risk_agent_blocks_when_global_circuit_breaker_is_enabled() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    agent.set_global_circuit_breaker(True)

    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "global_circuit_breaker_active")


def test_risk_agent_blocks_when_manual_pause_is_enabled() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    agent = _agent(now)
    agent.set_manual_pause(True)

    decision = agent.run(
        _prediction(),
        candidate=_candidate(updated_at=now),
        portfolio=_PortfolioView(total_exposure_usd=0.0, realized_pnl_usd=0.0, market_exposure_usd={}),
    )
    assert decision.approved is False
    _assert_rejected_with_reason(decision.reasoning, "manual_pause_active")
