"""Fetch BTC derivatives data from Binance Futures — incremental/append mode.

Downloads (appends only new records, never overwrites):
  1. Funding rate history (8h intervals, full history)
  2. Global long/short account ratio (5m/15m intervals, last 30 days API limit)
  3. Taker buy/sell volume ratio (5m/15m intervals, last 30 days API limit)
  4. Open interest history (5m/15m intervals, last 30 days API limit)
  5. Order book snapshot (real-time, single snapshot per run)

Run daily via Windows Task Scheduler to accumulate history over time.
After N days you will have N days of LS/taker/OI history (max 30 days API limit).

Usage:
    python scripts/btc_fetch_derivatives.py [--months 1] [--period 5m]
    python scripts/btc_fetch_derivatives.py --ob-only   # cron every 5 minutes
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

FAPI_BASE    = "https://fapi.binance.com"
SYMBOL       = "BTCUSDT"
THROTTLE_SEC = 0.3


def _fetch_json(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-market-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Incremental helpers
# ---------------------------------------------------------------------------

def _load_existing_timestamps(path: Path, ts_key: str = "timestamp_ms") -> tuple[set[int], int]:
    """Return (set of existing timestamps, max timestamp). Returns ({}, 0) if file missing."""
    if not path.exists():
        return set(), 0
    seen: set[int] = set()
    max_ts = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = int(rec.get(ts_key, 0))
                seen.add(ts)
                if ts > max_ts:
                    max_ts = ts
            except (json.JSONDecodeError, ValueError):
                continue
    return seen, max_ts


# ---------------------------------------------------------------------------
# Funding Rate (8h intervals, full history available)
# ---------------------------------------------------------------------------

def fetch_funding_rate(start_ms: int, end_ms: int, out_path: Path) -> int:
    seen_ts, max_existing = _load_existing_timestamps(out_path, "timestamp_ms")
    # Resume from last known record
    cursor_ms = max(start_ms, max_existing + 1) if max_existing else start_ms
    if cursor_ms >= end_ms:
        print(f"\n[1/4] Funding rate: already up to date ({len(seen_ts)} records)")
        return 0

    print(f"\n[1/4] Funding rate ({SYMBOL}) — from {datetime.fromtimestamp(cursor_ms/1000, UTC):%Y-%m-%d} ...")
    total = 0
    with out_path.open("a", encoding="utf-8") as fh:
        while cursor_ms < end_ms:
            url = (
                f"{FAPI_BASE}/fapi/v1/fundingRate"
                f"?symbol={SYMBOL}&startTime={cursor_ms}&endTime={end_ms}&limit=1000"
            )
            try:
                rows = _fetch_json(url)
            except Exception as exc:
                print(f"  Error: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not rows:
                break

            new = 0
            for row in rows:
                ts = int(row["fundingTime"])
                if ts in seen_ts:
                    continue
                record = {
                    "timestamp_ms": ts,
                    "funding_rate": float(row["fundingRate"]),
                    "mark_price":   float(row.get("markPrice", 0)),
                }
                fh.write(json.dumps(record) + "\n")
                seen_ts.add(ts)
                new += 1

            total += new
            cursor_ms = int(rows[-1]["fundingTime"]) + 1
            if new:
                print(f"  +{new} new records (total appended: {total})")
            if len(rows) < 1000:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: +{total} funding rate records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Long/Short Account Ratio (5m granularity, ~30 day API limit)
# ---------------------------------------------------------------------------

def fetch_long_short_ratio(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    seen_ts, max_existing = _load_existing_timestamps(out_path)
    cursor_ms = max(start_ms, max_existing + 1) if max_existing else start_ms
    if cursor_ms >= end_ms:
        print(f"\n[2/4] Long/short ratio: already up to date ({len(seen_ts)} records)")
        return 0

    print(f"\n[2/4] Long/short ratio ({SYMBOL}, {period}) — from {datetime.fromtimestamp(cursor_ms/1000, UTC):%Y-%m-%d} ...")
    total = 0
    retries = 0
    with out_path.open("a", encoding="utf-8") as fh:
        while cursor_ms < end_ms:
            url = (
                f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio"
                f"?symbol={SYMBOL}&period={period}&startTime={cursor_ms}&endTime={end_ms}&limit=500"
            )
            try:
                rows = _fetch_json(url)
                retries = 0
            except Exception as exc:
                retries += 1
                if retries >= 3:
                    print(f"  Giving up after {retries} retries: {exc}")
                    break
                print(f"  Error: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not rows:
                break

            new = 0
            for row in rows:
                ts = int(row["timestamp"])
                if ts in seen_ts:
                    continue
                record = {
                    "timestamp_ms":    ts,
                    "longShortRatio":  float(row["longShortRatio"]),
                    "longAccount":     float(row["longAccount"]),
                    "shortAccount":    float(row["shortAccount"]),
                }
                fh.write(json.dumps(record) + "\n")
                seen_ts.add(ts)
                new += 1

            total += new
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            if new:
                print(f"  +{new} new records (total appended: {total})")
            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: +{total} long/short records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Taker Buy/Sell Ratio (5m granularity, ~30 day API limit)
# ---------------------------------------------------------------------------

def fetch_taker_ratio(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    seen_ts, max_existing = _load_existing_timestamps(out_path)
    cursor_ms = max(start_ms, max_existing + 1) if max_existing else start_ms
    if cursor_ms >= end_ms:
        print(f"\n[3/4] Taker ratio: already up to date ({len(seen_ts)} records)")
        return 0

    print(f"\n[3/4] Taker ratio ({SYMBOL}, {period}) — from {datetime.fromtimestamp(cursor_ms/1000, UTC):%Y-%m-%d} ...")
    total = 0
    retries = 0
    with out_path.open("a", encoding="utf-8") as fh:
        while cursor_ms < end_ms:
            url = (
                f"{FAPI_BASE}/futures/data/takerlongshortRatio"
                f"?symbol={SYMBOL}&period={period}&startTime={cursor_ms}&endTime={end_ms}&limit=500"
            )
            try:
                rows = _fetch_json(url)
                retries = 0
            except Exception as exc:
                retries += 1
                if retries >= 3:
                    print(f"  Giving up after {retries} retries: {exc}")
                    break
                print(f"  Error: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not rows:
                break

            new = 0
            for row in rows:
                ts = int(row["timestamp"])
                if ts in seen_ts:
                    continue
                record = {
                    "timestamp_ms": ts,
                    "buySellRatio": float(row["buySellRatio"]),
                    "buyVol":       float(row["buyVol"]),
                    "sellVol":      float(row["sellVol"]),
                }
                fh.write(json.dumps(record) + "\n")
                seen_ts.add(ts)
                new += 1

            total += new
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            if new:
                print(f"  +{new} new records (total appended: {total})")
            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: +{total} taker ratio records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Open Interest (5m granularity, ~30 day API limit)
# ---------------------------------------------------------------------------

def fetch_open_interest(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    seen_ts, max_existing = _load_existing_timestamps(out_path)
    cursor_ms = max(start_ms, max_existing + 1) if max_existing else start_ms
    if cursor_ms >= end_ms:
        print(f"\n[4/4] Open interest: already up to date ({len(seen_ts)} records)")
        return 0

    print(f"\n[4/4] Open interest ({SYMBOL}, {period}) — from {datetime.fromtimestamp(cursor_ms/1000, UTC):%Y-%m-%d} ...")
    total = 0
    retries = 0
    with out_path.open("a", encoding="utf-8") as fh:
        while cursor_ms < end_ms:
            url = (
                f"{FAPI_BASE}/futures/data/openInterestHist"
                f"?symbol={SYMBOL}&period={period}&startTime={cursor_ms}&endTime={end_ms}&limit=500"
            )
            try:
                rows = _fetch_json(url)
                retries = 0
            except Exception as exc:
                retries += 1
                if retries >= 3:
                    print(f"  Giving up after {retries} retries: {exc}")
                    break
                print(f"  Error: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not rows:
                break

            new = 0
            for row in rows:
                ts = int(row["timestamp"])
                if ts in seen_ts:
                    continue
                record = {
                    "timestamp_ms":  ts,
                    "open_interest": float(row["sumOpenInterest"]),
                    "oi_value_usd":  float(row["sumOpenInterestValue"]),
                }
                fh.write(json.dumps(record) + "\n")
                seen_ts.add(ts)
                new += 1

            total += new
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            if new:
                print(f"  +{new} new records (total appended: {total})")
            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: +{total} open interest records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Order Book Snapshot (real-time, append one snapshot per run)
# ---------------------------------------------------------------------------

def fetch_order_book_snapshot(out_path: Path, depth: int = 20) -> int:
    url = f"https://api.binance.com/api/v3/depth?symbol={SYMBOL}&limit={depth}"
    try:
        data = _fetch_json(url)
    except Exception as exc:
        print(f"  Order book error: {exc}")
        return 0

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_vol   = sum(float(b[1]) for b in bids)
    ask_vol   = sum(float(a[1]) for a in asks)
    total_vol = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
    best_bid  = float(bids[0][0]) if bids else 0.0
    best_ask  = float(asks[0][0]) if asks else 0.0
    spread_bps = ((best_ask - best_bid) / best_bid * 10000) if best_bid > 0 else 0.0

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    record = {
        "timestamp_ms": now_ms,
        "imbalance":    round(imbalance, 6),
        "bid_volume":   round(bid_vol, 4),
        "ask_volume":   round(ask_vol, 4),
        "best_bid":     best_bid,
        "best_ask":     best_ask,
        "spread_bps":   round(spread_bps, 2),
    }
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    print(f"  OB snapshot: imbalance={imbalance:+.4f}  spread={spread_bps:.1f}bps -> {out_path}")
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--months", type=int, default=1,
                        help="Months of history to request (default: 1; API limit for LS/taker/OI: 30d)")
    parser.add_argument("--period", default="5m",
                        help="Granularity for LS/taker/OI: 5m, 15m, 30m, 1h (default: 5m)")
    parser.add_argument("--ob-only", action="store_true",
                        help="Only capture an order book snapshot (fast, for cron every 5m)")
    args = parser.parse_args()

    out_dir = Path("data/btc")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.ob_only:
        fetch_order_book_snapshot(out_dir / "order_book_snapshots.jsonl")
        return

    now_ms    = int(datetime.now(UTC).timestamp() * 1000)
    start_ms  = int((datetime.now(UTC) - timedelta(days=30 * args.months)).timestamp() * 1000)
    # LS/taker/OI: Binance API hard limit ~30 days regardless of requested range
    limit_start_ms = int((datetime.now(UTC) - timedelta(days=29)).timestamp() * 1000)

    print(f"Fetching BTC derivatives (incremental append mode)")
    print(f"  funding rate : from {datetime.fromtimestamp(start_ms/1000, UTC):%Y-%m-%d} (full history)")
    print(f"  ls/taker/OI  : from {datetime.fromtimestamp(limit_start_ms/1000, UTC):%Y-%m-%d} (API limit: 30d)")
    print(f"  period       : {args.period}")

    n1 = fetch_funding_rate(start_ms, now_ms, out_dir / "funding_rate.jsonl")
    n2 = fetch_long_short_ratio(limit_start_ms, now_ms, out_dir / "long_short_ratio.jsonl", args.period)
    n3 = fetch_taker_ratio(limit_start_ms, now_ms, out_dir / "taker_ratio.jsonl", args.period)
    n4 = fetch_open_interest(limit_start_ms, now_ms, out_dir / "open_interest.jsonl", args.period)
    n5 = fetch_order_book_snapshot(out_dir / "order_book_snapshots.jsonl")

    print(f"\nAll done: +funding={n1}  +ls={n2}  +taker={n3}  +oi={n4}  +ob={n5}")

    # Show total accumulated records
    def _count(fname: str) -> int:
        p = out_dir / fname
        return sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0

    print(f"Accumulated totals: "
          f"funding={_count('funding_rate.jsonl')}  "
          f"ls={_count('long_short_ratio.jsonl')}  "
          f"taker={_count('taker_ratio.jsonl')}  "
          f"oi={_count('open_interest.jsonl')}")


if __name__ == "__main__":
    main()
