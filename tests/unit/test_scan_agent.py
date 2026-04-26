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
    title: str | None = None,
) -> MarketSnapshot:
    return MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue="polymarket",
        title=title if title is not None else f"Mock market {market_id}",
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


def test_scan_agent_keeps_only_sweet_spot_btc_updown_slot() -> None:
    """BTC Up/Down series markets must collapse to a single slot per tick —
    the IN-PROGRESS slot within the model sweet spot (0.15-0.50 h to
    resolution). Slots closer than 0.15 h (model blind to intra-candle state)
    and further than 0.50 h (reference price anchor not set yet) are dropped.
    Within the sweet spot we prefer the slot with the SMALLEST h_to_res —
    that's the one whose reference price is already locked.
    Non-BTC markets pass through unchanged."""
    settings = ScanSettings(
        min_liquidity_usd=5_000,
        min_volume_24h_usd=0,
        min_hours_to_resolution=0,
        max_spread_bps=300,
    )
    agent = ScanAgent(settings)

    markets = [
        _mock_market(
            "btc-slot-too-far",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.75,
            title="Bitcoin Up or Down - April 22 16:45 UTC",
        ),
        _mock_market(
            "btc-slot-too-near",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.08,  # last 5 min, model blind
            title="Bitcoin Up or Down - April 22 16:00 UTC",
        ),
        _mock_market(
            "btc-slot-sweet-future",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.45,  # upcoming slot — anchor not set yet
            title="Bitcoin Up or Down - April 22 16:30 UTC",
        ),
        _mock_market(
            "btc-slot-sweet-inprogress",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.18,  # in-progress, anchor locked — best
            title="Bitcoin Up or Down - April 22 16:15 UTC",
        ),
        _mock_market(
            "other-market",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=5.0,
            title="Some unrelated Polymarket event",
        ),
    ]

    ranked = agent.run(markets)
    ids = {c.market.market_id for c in ranked}
    assert "btc-slot-sweet-inprogress" in ids
    assert "btc-slot-sweet-future" not in ids  # upcoming slot (no anchor) dropped
    assert "btc-slot-too-far" not in ids
    assert "btc-slot-too-near" not in ids
    assert "other-market" in ids
    assert len(ranked) == 2


def test_scan_agent_drops_btc_when_no_slot_in_sweet_spot() -> None:
    """If no BTC slot is in the sweet spot, BTC is skipped entirely this tick —
    no trade is better than a coin-flip trade."""
    settings = ScanSettings(
        min_liquidity_usd=5_000,
        min_volume_24h_usd=0,
        min_hours_to_resolution=0,
        max_spread_bps=300,
    )
    agent = ScanAgent(settings)

    markets = [
        _mock_market(
            "btc-slot-too-near",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.05,
            title="Bitcoin Up or Down - April 22 16:00 UTC",
        ),
        _mock_market(
            "btc-slot-too-far",
            liquidity_usd=10_000,
            spread_bps=100,
            volume_24h_usd=0,
            hours_to_resolution=0.75,
            title="Bitcoin Up or Down - April 22 16:45 UTC",
        ),
    ]

    ranked = agent.run(markets)
    assert ranked == []
