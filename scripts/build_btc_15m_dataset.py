"""Build a leakage-safe BTC Up/Down 15m feature dataset.

Pipeline:
  1. Read raw BTC events (data/btc15m/events_raw.jsonl) — output of the
     paginated /events?series_id=10192 fetch.
  2. For each event, derive:
       slot_close   = market.endDate
       slot_start   = slot_close - 15min
       decision_ts  = slot_start + 15s  (when the bot would have been able
                                          to fill, matching live behaviour)
       label_yes    = 1 if outcomePrices[0] == "1" else 0
  3. Bulk-fetch Binance 15m klines for the entire date range, cached on
     disk (data/btc15m/binance_15m_klines.jsonl).
  4. For each slot, compute the 4 BTC technical features that the
     production model schema (btc-v2-15m) requires:
       f_btc_rsi_14          — Wilder RSI of last 14 closes
       f_btc_bb_position_20  — Bollinger band position over last 20 closes
       f_btc_prev_return_1c  — log return of the last closed candle
       f_btc_prev_return_3c  — log return over the last 3 candles
     All computed strictly on candles whose close_time < decision_ts
     (no look-ahead).
  5. Merge with the 36 generic features from the existing feature_rows.jsonl
     where available; otherwise emit a row with the BTC features only.
  6. Output: data/btc15m/feature_rows_btc.jsonl (one row per slot, schema
     btc-v2-15m).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RAW_EVENTS = ROOT / "data" / "btc15m" / "events_raw.jsonl"
KLINES_CACHE = ROOT / "data" / "btc15m" / "binance_15m_klines.jsonl"
OUT_FEATURES = ROOT / "data" / "btc15m" / "feature_rows_btc.jsonl"


def _utc(iso: str) -> datetime:
    s = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def load_events() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(RAW_EVENTS, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    return rows


def derive_slots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each event carries one market.  Extract the slot tuple we need."""
    slots: list[dict[str, Any]] = []
    for ev in events:
        markets = ev.get("markets") or []
        if not markets:
            continue
        m = markets[0]
        if not m.get("closed"):
            continue
        end_iso = m.get("endDate") or ev.get("endDate")
        if not end_iso:
            continue
        try:
            end_dt = _utc(end_iso)
        except ValueError:
            continue
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (ValueError, TypeError):
                prices = None
        if not isinstance(prices, list) or len(prices) < 2:
            continue
        try:
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except (ValueError, TypeError):
            continue
        # Resolved binary outcome only.
        if not (
            (yes_price > 0.95 and no_price < 0.05)
            or (no_price > 0.95 and yes_price < 0.05)
        ):
            continue
        label_yes = 1 if yes_price > 0.5 else 0
        slot_start = end_dt - timedelta(minutes=15)
        decision_ts = slot_start + timedelta(seconds=15)
        slots.append(
            {
                "market_id": str(m.get("id") or m.get("conditionId") or ""),
                "event_id": str(ev.get("id") or ""),
                "slot_close_utc": end_dt.isoformat().replace("+00:00", "Z"),
                "slot_start_utc": slot_start.isoformat().replace("+00:00", "Z"),
                "decision_timestamp_utc": decision_ts.isoformat().replace(
                    "+00:00", "Z"
                ),
                "label_yes": label_yes,
                "title": m.get("question") or "",
                "yes_price_close": yes_price,
                "no_price_close": no_price,
            }
        )
    slots.sort(key=lambda s: s["decision_timestamp_utc"])
    return slots


def fetch_binance_klines(start_dt: datetime, end_dt: datetime) -> list[list[Any]]:
    """Fetch 15m BTCUSDT klines from Binance, in 1000-bar pages."""
    all_klines: list[list[Any]] = []
    cursor_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    BAR_MS = 15 * 60 * 1000
    BATCH = 1000
    n_batches = 0
    while cursor_ms < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol=BTCUSDT&interval=15m&limit={BATCH}&startTime={cursor_ms}"
        )
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "pmbot/0.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                payload = json.loads(r.read())
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            print(f"  retry on {e}", file=sys.stderr)
            time.sleep(1.0)
            continue
        if not isinstance(payload, list) or not payload:
            break
        all_klines.extend(payload)
        last_open_ms = int(payload[-1][0])
        next_ms = last_open_ms + BAR_MS
        if next_ms <= cursor_ms:
            break
        cursor_ms = next_ms
        n_batches += 1
        if n_batches % 10 == 0:
            print(
                f"  fetched {len(all_klines)} klines through "
                f"{datetime.fromtimestamp(last_open_ms/1000, UTC).isoformat()}",
                flush=True,
            )
        time.sleep(0.05)
    return all_klines


def load_or_fetch_klines(slots: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """Return a dict open_time_ms -> {open, high, low, close, volume}.

    Cached on disk so repeated runs don't re-hit Binance."""
    by_open_ms: dict[int, dict[str, float]] = {}
    if KLINES_CACHE.exists():
        with open(KLINES_CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    by_open_ms[int(row["open_time_ms"])] = row
                except (ValueError, TypeError, KeyError):
                    continue
        print(f"loaded {len(by_open_ms)} klines from cache")

    if not slots:
        return by_open_ms

    earliest = _utc(slots[0]["decision_timestamp_utc"]) - timedelta(hours=8)
    latest = _utc(slots[-1]["decision_timestamp_utc"]) + timedelta(hours=1)

    have_min = min(by_open_ms) if by_open_ms else None
    have_max = max(by_open_ms) if by_open_ms else None
    need_start = earliest
    need_end = latest
    if have_min is not None:
        # Trust cache to be contiguous; only fetch outside its range.
        cache_start_dt = datetime.fromtimestamp(have_min / 1000, UTC)
        cache_end_dt = datetime.fromtimestamp(have_max / 1000 + 900, UTC)
        if cache_start_dt <= earliest and cache_end_dt >= latest:
            return by_open_ms
        if cache_start_dt > earliest:
            need_end = cache_start_dt
        elif cache_end_dt < latest:
            need_start = cache_end_dt

    print(f"fetching klines {need_start.isoformat()} -> {need_end.isoformat()}")
    klines = fetch_binance_klines(need_start, need_end)
    print(f"  fetched {len(klines)} new klines")

    KLINES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(KLINES_CACHE, "a", encoding="utf-8") as f:
        for k in klines:
            row = {
                "open_time_ms": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            by_open_ms[row["open_time_ms"]] = row
            f.write(json.dumps(row) + "\n")
    return by_open_ms


def _wilder_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _bb_position(closes: list[float], period: int = 20) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    std = math.sqrt(var)
    if std <= 0:
        return 0.5
    upper = mean + 2 * std
    lower = mean - 2 * std
    pos = (closes[-1] - lower) / (upper - lower)
    return max(0.0, min(1.0, pos))


def _log_return(c1: float, c0: float) -> float:
    if c0 <= 0 or c1 <= 0:
        return 0.0
    return math.log(c1 / c0)


def compute_features(
    decision_ts: datetime, klines_by_open: dict[int, dict[str, float]]
) -> dict[str, float] | None:
    """Compute the 4 BTC technical features at decision_ts.

    A 15m candle with open_time_ms = T closes at T + 15min.  We use only
    candles whose close_time ≤ decision_ts (no look-ahead) — i.e.
    open_time_ms + 900_000 ≤ decision_ts_ms.
    """
    decision_ms = int(decision_ts.timestamp() * 1000)
    cutoff = decision_ms - 900_000  # latest open_time we may use
    # Collect last 25 closed candles.
    candidates: list[dict[str, float]] = []
    cursor = cutoff - cutoff % 900_000  # snap to 15m boundary
    for _ in range(40):
        row = klines_by_open.get(cursor)
        if row is not None:
            candidates.append(row)
        cursor -= 900_000
    if len(candidates) < 21:
        return None
    candidates.reverse()  # oldest first
    closes = [c["close"] for c in candidates]
    rsi14 = _wilder_rsi(closes, 14)
    bb20 = _bb_position(closes, 20)
    if rsi14 is None or bb20 is None:
        return None
    last_close = closes[-1]
    prev_close = closes[-2]
    prev3_close = closes[-4] if len(closes) >= 4 else closes[0]
    return {
        "f_btc_rsi_14": rsi14,
        "f_btc_bb_position_20": bb20,
        "f_btc_prev_return_1c": _log_return(last_close, prev_close),
        "f_btc_prev_return_3c": _log_return(last_close, prev3_close),
    }


def merge_with_existing_features(
    btc_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Optionally enrich each BTC slot with the 36 generic features
    already computed by the standard pipeline (where the same market_id
    exists).  If not, emit BTC-only rows."""
    existing_by_id: dict[str, dict[str, Any]] = {}
    existing_path = (
        ROOT
        / "data"
        / "strategy-research"
        / "historical-markets"
        / "derived"
        / "feature_store"
        / "v1"
        / "feature_rows.jsonl"
    )
    if existing_path.exists():
        with open(existing_path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    mid = str(row.get("market_id"))
                    if mid:
                        existing_by_id[mid] = row
                except (ValueError, TypeError):
                    continue
    enriched: list[dict[str, Any]] = []
    for row in btc_rows:
        merged = dict(row)
        existing = existing_by_id.get(str(row["market_id"]))
        if existing:
            for k, v in existing.items():
                if k.startswith("f_") and k not in merged:
                    merged[k] = v
        merged["feature_schema_version"] = "btc-v2-15m"
        enriched.append(merged)
    return enriched


def main() -> int:
    if not RAW_EVENTS.exists():
        print(f"!! missing {RAW_EVENTS} — run the events fetch first", file=sys.stderr)
        return 1
    print("Loading events…")
    events = load_events()
    print(f"  events: {len(events)}")
    slots = derive_slots(events)
    print(f"  resolved binary slots: {len(slots)}")
    if not slots:
        return 1

    print("Loading/fetching Binance klines…")
    klines = load_or_fetch_klines(slots)
    print(f"  klines indexed: {len(klines)}")

    print("Computing features per slot…")
    rows: list[dict[str, Any]] = []
    skipped = 0
    for s in slots:
        decision_ts = _utc(s["decision_timestamp_utc"])
        feats = compute_features(decision_ts, klines)
        if feats is None:
            skipped += 1
            continue
        row_id = f"{s['market_id']}@{s['decision_timestamp_utc']}"
        row = {
            "row_id": row_id,
            "market_id": s["market_id"],
            "event_id": s["event_id"],
            "category": "Crypto",
            "decision_timestamp_utc": s["decision_timestamp_utc"],
            "label_yes": s["label_yes"],
            "data_quality_flags": [],
            **feats,
        }
        rows.append(row)
    print(f"  rows built: {len(rows)} (skipped {skipped} for missing klines)")

    print("Merging with existing generic features…")
    rows = merge_with_existing_features(rows)
    label_pos = sum(r["label_yes"] for r in rows)
    print(f"  label balance: YES={label_pos} ({label_pos/len(rows)*100:.1f}%) / "
          f"NO={len(rows)-label_pos} ({(len(rows)-label_pos)/len(rows)*100:.1f}%)")

    OUT_FEATURES.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FEATURES, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"WROTE {OUT_FEATURES}  ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
