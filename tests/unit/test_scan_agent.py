from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.app.settings import ScanSettings
from prediction_market_bot.domain.enums import MarketStatus
from prediction_market_bot.domain.models import MarketSnapshot


def _mock_market(
    market_id: str,
    *,
    liquidity_usd: float,
    spread_bps: int,
    volume_24h_usd: float,
    hours_to_resolution: float,
    status: MarketStatus = MarketStatus.OPEN,
) -> MarketSnapshot:
    return MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue="polymarket",
        title=f"Mock market {market_id}",
        yes_price=0.55,
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        spread_bps=spread_bps,
        hours_to_resolution=hours_to_resolution,
        last_price_move_bps=0,
        category="test",
        status=status,
    )


def test_scan_agent_filters_and_ranks_markets() -> None:
    settings = ScanSettings(
        min_liquidity_usd=10_000,
        min_volume_24h_usd=5_000,
        min_hours_to_resolution=6,
        max_spread_bps=300,
    )
    agent = ScanAgent(settings)

    markets = [
        _mock_market(
            "m-best",
            liquidity_usd=80_000,
            spread_bps=80,
            volume_24h_usd=50_000,
            hours_to_resolution=8,
        ),
        _mock_market(
            "m-mid",
            liquidity_usd=30_000,
            spread_bps=140,
            volume_24h_usd=18_000,
            hours_to_resolution=24,
        ),
        _mock_market(  # filtered: spread too wide
            "m-wide",
            liquidity_usd=90_000,
            spread_bps=450,
            volume_24h_usd=50_000,
            hours_to_resolution=10,
        ),
        _mock_market(  # filtered: status closed
            "m-closed",
            liquidity_usd=90_000,
            spread_bps=90,
            volume_24h_usd=50_000,
            hours_to_resolution=10,
            status=MarketStatus.CLOSED,
        ),
    ]

    ranked = agent.run(markets)
    assert [candidate.market.market_id for candidate in ranked] == ["m-best", "m-mid"]
    assert ranked[0].scan_score > ranked[1].scan_score


def test_scan_agent_ranking_is_deterministic_on_ties() -> None:
    settings = ScanSettings(
        min_liquidity_usd=10_000,
        min_volume_24h_usd=5_000,
        min_hours_to_resolution=6,
        max_spread_bps=300,
    )
    agent = ScanAgent(settings)

    # Identical metrics: tie must be resolved by market_id.
    m_a = _mock_market(
        "m-a",
        liquidity_usd=20_000,
        spread_bps=100,
        volume_24h_usd=10_000,
        hours_to_resolution=12,
    )
    m_b = _mock_market(
        "m-b",
        liquidity_usd=20_000,
        spread_bps=100,
        volume_24h_usd=10_000,
        hours_to_resolution=12,
    )

    ranked = agent.run([m_b, m_a])
    assert [candidate.market.market_id for candidate in ranked] == ["m-a", "m-b"]
