"""Fetch BTC derivatives data from Binance Futures for model training.

Downloads:
  1. Funding rate history (8h intervals, full history)
  2. Global long/short account ratio (5m/1h intervals)
  3. Taker buy/sell volume ratio (5m/1h intervals)

Usage:
    python scripts/btc_fetch_derivatives.py [--months 24]

Output:
    data/btc/funding_rate.jsonl
    data/btc/long_short_ratio.jsonl
    data/btc/taker_ratio.jsonl
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

FAPI_BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
THROTTLE_SEC = 0.3


def _fetch_json(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-market-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Funding Rate (8h intervals, up to 1000 per request)
# ---------------------------------------------------------------------------

def fetch_funding_rate(start_ms: int, end_ms: int, out_path: Path) -> int:
    print(f"\n[1/3] Funding rate ({SYMBOL}) ...")
    total = 0
    cursor_ms = start_ms
    with out_path.open("w", encoding="utf-8") as fh:
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

            for row in rows:
                record = {
                    "timestamp_ms": int(row["fundingTime"]),
                    "funding_rate": float(row["fundingRate"]),
                    "mark_price": float(row.get("markPrice", 0)),
                }
                fh.write(json.dumps(record) + "\n")

            total += len(rows)
            cursor_ms = int(rows[-1]["fundingTime"]) + 1
            pct = min((cursor_ms - start_ms) / max(end_ms - start_ms, 1) * 100, 100)
            print(f"  {total:>6} records  {pct:5.1f}%")

            if len(rows) < 1000:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} funding rate records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Long/Short Account Ratio (5m granularity, up to 500 per request)
# ---------------------------------------------------------------------------

def fetch_long_short_ratio(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    print(f"\n[2/3] Long/short account ratio ({SYMBOL}, period={period}) ...")
    total = 0
    cursor_ms = start_ms
    retries = 0
    with out_path.open("w", encoding="utf-8") as fh:
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

            for row in rows:
                record = {
                    "timestamp_ms": int(row["timestamp"]),
                    "long_short_ratio": float(row["longShortRatio"]),
                    "long_account": float(row["longAccount"]),
                    "short_account": float(row["shortAccount"]),
                }
                fh.write(json.dumps(record) + "\n")

            total += len(rows)
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            pct = min((cursor_ms - start_ms) / max(end_ms - start_ms, 1) * 100, 100)
            print(f"  {total:>6} records  {pct:5.1f}%")

            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} long/short records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Taker Buy/Sell Ratio (5m granularity, up to 500 per request)
# ---------------------------------------------------------------------------

def fetch_taker_ratio(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    print(f"\n[3/3] Taker buy/sell ratio ({SYMBOL}, period={period}) ...")
    total = 0
    cursor_ms = start_ms
    retries = 0
    with out_path.open("w", encoding="utf-8") as fh:
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

            for row in rows:
                record = {
                    "timestamp_ms": int(row["timestamp"]),
                    "buy_sell_ratio": float(row["buySellRatio"]),
                    "buy_vol": float(row["buyVol"]),
                    "sell_vol": float(row["sellVol"]),
                }
                fh.write(json.dumps(record) + "\n")

            total += len(rows)
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            pct = min((cursor_ms - start_ms) / max(end_ms - start_ms, 1) * 100, 100)
            print(f"  {total:>6} records  {pct:5.1f}%")

            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} taker ratio records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Open Interest (5m granularity, last 30 days)
# ---------------------------------------------------------------------------

def fetch_open_interest(start_ms: int, end_ms: int, out_path: Path, period: str = "5m") -> int:
    """Fetch open interest history. API limit: 30 days."""
    print(f"\n[4/5] Open interest ({SYMBOL}, period={period}) ...")
    total = 0
    cursor_ms = start_ms
    retries = 0
    with out_path.open("w", encoding="utf-8") as fh:
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

            for row in rows:
                record = {
                    "timestamp_ms":   int(row["timestamp"]),
                    "open_interest":  float(row["sumOpenInterest"]),
                    "oi_value_usd":   float(row["sumOpenInterestValue"]),
                }
                fh.write(json.dumps(record) + "\n")

            total += len(rows)
            cursor_ms = int(rows[-1]["timestamp"]) + 1
            pct = min((cursor_ms - start_ms) / max(end_ms - start_ms, 1) * 100, 100)
            print(f"  {total:>6} records  {pct:5.1f}%")

            if len(rows) < 500:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} open interest records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Order Book Imbalance — spot snapshots (real-time, captured periodically)
# ---------------------------------------------------------------------------

def fetch_order_book_snapshots(out_path: Path, depth: int = 20, n_snapshots: int = 8640) -> int:
    """Fetch order book snapshots and compute bid/ask imbalance.

    imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)
    Range: [-1, 1]. Positive = more buy pressure.

    Note: this captures the CURRENT order book. Run this script repeatedly
    (e.g. every 5m via cron) to build up historical data. A single run
    captures 1 snapshot with current market state.
    """
    print(f"\n[5/5] Order book snapshot ({SYMBOL}, depth={depth}) ...")
    url = f"https://api.binance.com/api/v3/depth?symbol={SYMBOL}&limit={depth}"
    try:
        data = _fetch_json(url)
    except Exception as exc:
        print(f"  Error: {exc}")
        return 0

    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)
    total_vol = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
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

    # Append to existing file
    mode = "a" if out_path.exists() else "w"
    with out_path.open(mode, encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    print(f"  Snapshot: imbalance={imbalance:+.4f}  spread={spread_bps:.1f}bps -> {out_path}")
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=24, help="Months of history (default: 24)")
    parser.add_argument("--period", default="5m",
                        help="Granularity for LS/taker/OI: 5m, 15m, 30m, 1h, 4h (default: 5m)")
    parser.add_argument("--ob-only", action="store_true",
                        help="Only capture an order book snapshot (fast, use in cron every 5m)")
    args = parser.parse_args()

    out_dir = Path("data/btc")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.ob_only:
        fetch_order_book_snapshots(out_dir / "order_book_snapshots.jsonl")
        return

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=30 * args.months)).timestamp() * 1000)

    # Binance Futures historical endpoints: last 30 days only.
    # Funding rate supports full history.
    limit_start_ms = int((datetime.now(UTC) - timedelta(days=29)).timestamp() * 1000)

    print(f"Fetching BTC derivatives data")
    print(f"  funding rate : {datetime.fromtimestamp(start_ms / 1000, UTC).strftime('%Y-%m-%d')} to now (full history)")
    print(f"  ls/taker/OI  : {datetime.fromtimestamp(limit_start_ms / 1000, UTC).strftime('%Y-%m-%d')} to now (API limit: 30d)")
    print(f"  period       : {args.period}")

    n1 = fetch_funding_rate(start_ms, now_ms, out_dir / "funding_rate.jsonl")
    n2 = fetch_long_short_ratio(limit_start_ms, now_ms, out_dir / "long_short_ratio.jsonl", period=args.period)
    n3 = fetch_taker_ratio(limit_start_ms, now_ms, out_dir / "taker_ratio.jsonl", period=args.period)
    n4 = fetch_open_interest(limit_start_ms, now_ms, out_dir / "open_interest.jsonl", period=args.period)
    n5 = fetch_order_book_snapshots(out_dir / "order_book_snapshots.jsonl")

    print(f"\nAll done: funding={n1}  ls={n2}  taker={n3}  oi={n4}  ob={n5}")
    print(f"\nTip: run the following every 5 minutes to build order book history:")
    print(f"  python scripts/btc_fetch_derivatives.py --ob-only")


if __name__ == "__main__":
    main()
