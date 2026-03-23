from __future__ import annotations

from prediction_market_bot.strategy_research.data_ingest.normalizer import (
    normalize_market,
    normalize_market_snapshot,
    normalize_resolution,
    normalize_trade,
)


def test_normalize_market_extracts_core_fields() -> None:
    payload = {
        "id": "mkt-1",
        "question": "Will X happen?",
        "category": "macro",
        "status": "resolved",
        "resolution": "yes",
        "resolutionDate": "2026-01-10T12:00:00Z",
        "endDate": "2026-01-10T10:00:00Z",
        "outcomePrices": [0.61, 0.39],
        "liquidity": "12000",
        "volume24hr": "2300",
    }

    row = normalize_market(payload, event_id="evt-1")
    assert row["market_id"] == "mkt-1"
    assert row["event_id"] == "evt-1"
    assert row["resolved_outcome"] == "YES"
    assert row["yes_price_last"] == 0.61
    assert row["liquidity_usd"] == 12000.0
    assert row["volume_24h_usd"] == 2300.0
    assert isinstance(row["resolved_at_utc"], str)


def test_normalize_snapshot_trade_resolution_rows() -> None:
    snapshot = normalize_market_snapshot(
        {"timestamp": "2026-01-09T10:00:00Z", "outcomePrices": [0.42, 0.58], "bestBid": "0.41", "bestAsk": "0.43"},
        market_id="mkt-1",
    )
    trade = normalize_trade(
        {"id": "trd-1", "timestamp": "2026-01-09T11:00:00Z", "price": "0.44", "size": "12", "side": "buy"},
        market_id="mkt-1",
    )
    resolution = normalize_resolution(
        {"outcome": "no", "resolutionDate": "2026-01-10T12:00:00Z", "status": "resolved"},
        market_id="mkt-1",
    )
    assert snapshot["market_id"] == "mkt-1"
    assert snapshot["yes_price"] == 0.42
    assert trade["trade_id"] == "trd-1"
    assert trade["price_yes"] == 0.44
    assert trade["side"] == "BUY"
    assert resolution["resolved_outcome"] == "NO"
    assert resolution["resolution_status"] == "RESOLVED"

