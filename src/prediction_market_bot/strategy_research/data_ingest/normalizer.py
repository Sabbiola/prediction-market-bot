from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping


def _parse_datetime(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat()


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _extract_yes_price(payload: Mapping[str, Any]) -> float | None:
    direct = _to_float(payload.get("yes_price"))
    if direct is not None:
        return min(max(direct, 0.0), 1.0)
    outcome_prices = payload.get("outcomePrices")
    if isinstance(outcome_prices, list) and outcome_prices:
        parsed = _to_float(outcome_prices[0])
        if parsed is not None:
            return min(max(parsed, 0.0), 1.0)
    return None


def _extract_yes_price_midmarket(payload: Mapping[str, Any]) -> float | None:
    """Extract a pre-resolution yes price, excluding terminal settlement prices (0 or 1).

    Polymarket Gamma API sets outcomePrices to ["1","0"] or ["0","1"] after settlement.
    Using those as the decision-time market price would make every label trivially predictable.
    We exclude prices that are ≥0.99 or ≤0.01 (settled), and fall back to None so the label
    builder can apply its own default (0.5).
    """
    # lastTradePrice reflects the final trade before close — skip if it looks settled
    last_trade = _to_float(payload.get("lastTradePrice"))
    if last_trade is not None and 0.02 <= last_trade <= 0.98:
        return last_trade
    outcome_prices = payload.get("outcomePrices")
    if isinstance(outcome_prices, str):
        import json as _json
        try:
            outcome_prices = _json.loads(outcome_prices)
        except Exception:
            outcome_prices = None
    if isinstance(outcome_prices, list) and outcome_prices:
        parsed = _to_float(outcome_prices[0])
        if parsed is not None and 0.02 <= parsed <= 0.98:
            return parsed
    return None


def normalize_event(event_payload: Mapping[str, Any]) -> dict[str, Any]:
    event_id = str(event_payload.get("id") or event_payload.get("event_id") or "").strip()
    return {
        "event_id": event_id,
        "title": str(event_payload.get("title") or event_payload.get("name") or "").strip(),
        "category": str(event_payload.get("category") or "").strip(),
        "slug": str(event_payload.get("slug") or "").strip(),
        "start_at_utc": _parse_datetime(event_payload.get("startDate") or event_payload.get("start_at")),
        "end_at_utc": _parse_datetime(event_payload.get("endDate") or event_payload.get("end_at")),
        "raw_source": "historical_event_metadata",
    }


def normalize_market(market_payload: Mapping[str, Any], *, event_id: str = "") -> dict[str, Any]:
    market_id = str(
        market_payload.get("id")
        or market_payload.get("market_id")
        or market_payload.get("conditionId")
        or market_payload.get("slug")
        or ""
    ).strip()
    resolved_outcome = str(
        market_payload.get("resolved_outcome")
        or market_payload.get("resolution")
        or market_payload.get("outcome")
        or ""
    ).strip()
    # Derive resolution from outcomePrices when explicit fields are absent (Polymarket Gamma API).
    # outcomePrices[0] = YES final price; 1.0 → resolved YES, 0.0 → resolved NO.
    if not resolved_outcome:
        outcome_prices = market_payload.get("outcomePrices")
        if isinstance(outcome_prices, (list, str)):
            if isinstance(outcome_prices, str):
                import json as _json
                try:
                    outcome_prices = _json.loads(outcome_prices)
                except Exception:
                    outcome_prices = []
            if outcome_prices:
                yes_final = _to_float(outcome_prices[0])
                if yes_final is not None and yes_final >= 0.99:
                    resolved_outcome = "YES"
                elif yes_final is not None and yes_final <= 0.01:
                    resolved_outcome = "NO"
    return {
        "market_id": market_id,
        "event_id": event_id or str(market_payload.get("event_id") or market_payload.get("eventId") or "").strip(),
        "question": str(market_payload.get("question") or market_payload.get("title") or "").strip(),
        "category": str(market_payload.get("category") or "").strip(),
        "status": str(market_payload.get("status") or "").strip().upper(),
        "resolved_outcome": resolved_outcome.upper(),
        "resolved_at_utc": _parse_datetime(
            market_payload.get("resolved_at")
            or market_payload.get("resolutionDate")
            or market_payload.get("resolution_date")
            or market_payload.get("closedTime")
        ),
        "close_at_utc": _parse_datetime(market_payload.get("endDate") or market_payload.get("closeDate")),
        "yes_price_last": _extract_yes_price_midmarket(market_payload),
        "liquidity_usd": _to_float(market_payload.get("liquidity")),
        "volume_24h_usd": _to_float(market_payload.get("volume24hr") or market_payload.get("volume24h")),
        "raw_source": "historical_market_metadata",
    }


def normalize_market_snapshot(snapshot_payload: Mapping[str, Any], *, market_id: str) -> dict[str, Any]:
    return {
        "market_id": market_id,
        "snapshot_at_utc": _parse_datetime(snapshot_payload.get("timestamp") or snapshot_payload.get("updatedAt")),
        "yes_price": _extract_yes_price(snapshot_payload),
        "best_bid": _to_float(snapshot_payload.get("bestBid") or snapshot_payload.get("best_bid")),
        "best_ask": _to_float(snapshot_payload.get("bestAsk") or snapshot_payload.get("best_ask")),
        "liquidity_usd": _to_float(snapshot_payload.get("liquidity")),
        "volume_24h_usd": _to_float(snapshot_payload.get("volume24hr") or snapshot_payload.get("volume24h")),
        "raw_source": "historical_market_snapshot",
    }


def normalize_orderbook_snapshot(payload: Mapping[str, Any], *, market_id: str) -> dict[str, Any]:
    return {
        "market_id": market_id,
        "snapshot_at_utc": _parse_datetime(payload.get("timestamp") or payload.get("updatedAt")),
        "best_bid": _to_float(payload.get("bestBid") or payload.get("best_bid")),
        "best_ask": _to_float(payload.get("bestAsk") or payload.get("best_ask")),
        "bid_size": _to_float(payload.get("bidSize") or payload.get("bid_size")),
        "ask_size": _to_float(payload.get("askSize") or payload.get("ask_size")),
        "raw_source": "historical_orderbook_snapshot",
    }


def normalize_trade(payload: Mapping[str, Any], *, market_id: str) -> dict[str, Any]:
    return {
        "market_id": market_id,
        "trade_id": str(payload.get("trade_id") or payload.get("id") or "").strip(),
        "timestamp_utc": _parse_datetime(payload.get("timestamp") or payload.get("createdAt")),
        "price_yes": _to_float(payload.get("price") or payload.get("price_yes")),
        "size": _to_float(payload.get("size") or payload.get("quantity")),
        "side": str(payload.get("side") or "").strip().upper(),
        "raw_source": "historical_trade",
    }


def normalize_resolution(payload: Mapping[str, Any], *, market_id: str) -> dict[str, Any]:
    resolved = str(payload.get("resolved_outcome") or payload.get("outcome") or payload.get("resolution") or "").strip()
    return {
        "market_id": market_id,
        "resolved_outcome": resolved.upper(),
        "resolved_at_utc": _parse_datetime(payload.get("resolved_at") or payload.get("resolutionDate")),
        "resolution_status": str(payload.get("status") or "").strip().upper(),
        "raw_source": "historical_resolution",
    }


def normalize_dataset_record_count(rows: list[dict[str, Any]], *, key_fields: tuple[str, ...]) -> tuple[int, int]:
    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for row in rows:
        key = tuple(str(row.get(field) or "").strip() for field in key_fields)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
    return len(seen), duplicates

