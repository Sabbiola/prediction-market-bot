"""Fetch cross-exchange BTC price data for model training.

Downloads:
  1. Coinbase BTC-USD 5m candles  (via api.exchange.coinbase.com)
  2. Kraken XBTUSD 5m candles     (via api.kraken.com)
  3. Alternative.me Fear & Greed Index daily history (max 4+ years)

Usage:
    python scripts/btc_fetch_cross_exchange.py [--months 24]

Output:
    data/btc/ohlcv_5m_coinbase.jsonl     (aligned with Binance 5m timestamps)
    data/btc/ohlcv_5m_kraken.jsonl
    data/btc/fear_greed_daily.jsonl

Cross-exchange basis features produced for training:
    f_btc_cb_basis_bps     = (coinbase_close - binance_close) / binance_close * 10000
    f_btc_kraken_basis_bps = (kraken_close  - binance_close) / binance_close * 10000
    f_btc_fear_greed       = fng_value / 100.0  (daily, same for all 5m candles in a day)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

THROTTLE_SEC = 0.3


def _fetch_json(url: str) -> object:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "prediction-market-bot/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Coinbase Exchange — 5m candles (max 300 per request, granularity=300s)
# ---------------------------------------------------------------------------

def fetch_coinbase_candles(start_ms: int, end_ms: int, out_path: Path) -> int:
    """Download BTC-USD 5m candles from Coinbase Exchange (formerly Coinbase Pro).

    API returns at most 300 candles per request. No authentication required.
    Format: [timestamp_sec, low, high, open, close, volume]
    """
    print(f"\n[1/3] Coinbase BTC-USD 5m candles ...")
    total = 0
    # Coinbase API uses seconds, not milliseconds
    cursor_sec = start_ms // 1000
    end_sec    = end_ms   // 1000
    BATCH_SEC  = 300 * 300  # 300 candles × 5min each = 25 hours per request

    with out_path.open("w", encoding="utf-8") as fh:
        while cursor_sec < end_sec:
            batch_end = min(cursor_sec + BATCH_SEC, end_sec)
            start_iso = datetime.fromtimestamp(cursor_sec, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso   = datetime.fromtimestamp(batch_end,  UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            url = (
                "https://api.exchange.coinbase.com/products/BTC-USD/candles"
                f"?granularity=300&start={start_iso}&end={end_iso}"
            )
            try:
                rows = _fetch_json(url)
            except Exception as exc:
                print(f"  Error: {exc}. Skipping batch...")
                cursor_sec = batch_end + 1
                time.sleep(1)
                continue

            if not isinstance(rows, list) or not rows:
                cursor_sec = batch_end + 1
                continue

            # Coinbase returns newest-first — sort ascending
            rows.sort(key=lambda r: r[0])

            for row in rows:
                record = {
                    "open_time_ms": int(row[0]) * 1000,
                    "open":         float(row[3]),
                    "high":         float(row[2]),
                    "low":          float(row[1]),
                    "close":        float(row[4]),
                    "volume":       float(row[5]),
                }
                fh.write(json.dumps(record) + "\n")

            total += len(rows)
            last_dt = datetime.fromtimestamp(rows[-1][0], UTC).strftime("%Y-%m-%d")
            pct = min((batch_end - start_ms // 1000) / max(end_sec - start_ms // 1000, 1) * 100, 100)
            print(f"  {total:>8} candles  {pct:5.1f}%  last={last_dt}")

            cursor_sec = batch_end + 1
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} Coinbase candles -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Kraken — 5m candles (max 720 per request, interval=5 min)
# ---------------------------------------------------------------------------

def fetch_kraken_candles(start_ms: int, end_ms: int, out_path: Path) -> int:
    """Download XBTUSD 5m candles from Kraken.

    API returns at most 720 candles per request, starting from 'since' timestamp.
    Format: [timestamp_sec, open, high, low, close, vwap, volume, count]
    """
    print(f"\n[2/3] Kraken XBTUSD 5m candles ...")
    total = 0
    cursor_sec = start_ms // 1000
    end_sec    = end_ms   // 1000

    with out_path.open("w", encoding="utf-8") as fh:
        while cursor_sec < end_sec:
            url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=5&since={cursor_sec}"
            try:
                data = _fetch_json(url)
            except Exception as exc:
                print(f"  Error: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not isinstance(data, dict) or data.get("error"):
                print(f"  API error: {data.get('error') if isinstance(data, dict) else data}")
                break

            result = data.get("result", {})
            rows = None
            last_ts = None
            for key, val in result.items():
                if key == "last":
                    last_ts = int(val)
                else:
                    rows = val

            if not rows:
                break

            for row in rows:
                ts_sec = int(row[0])
                if ts_sec >= end_sec:
                    break
                record = {
                    "open_time_ms": ts_sec * 1000,
                    "open":         float(row[1]),
                    "high":         float(row[2]),
                    "low":          float(row[3]),
                    "close":        float(row[4]),
                    "volume":       float(row[6]),
                }
                fh.write(json.dumps(record) + "\n")
                total += 1

            last_row_ts = int(rows[-1][0])
            pct = min((last_row_ts - cursor_sec) / max(end_sec - start_ms // 1000, 1) * 100, 100)
            last_dt = datetime.fromtimestamp(last_row_ts, UTC).strftime("%Y-%m-%d")
            print(f"  {total:>8} candles  {pct:5.1f}%  last={last_dt}")

            if last_ts is not None and last_ts > cursor_sec:
                cursor_sec = last_ts
            elif last_row_ts > cursor_sec:
                cursor_sec = last_row_ts + 1
            else:
                break

            if len(rows) < 720:
                break
            time.sleep(THROTTLE_SEC)

    print(f"  Done: {total} Kraken candles -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Alternative.me Fear & Greed Index — daily history
# ---------------------------------------------------------------------------

def fetch_fear_greed(out_path: Path, days: int = 1000) -> int:
    """Download Fear & Greed Index daily history from Alternative.me.

    API supports up to ~4 years of history in one request (limit parameter).
    """
    print(f"\n[3/3] Fear & Greed Index (last {days} days) ...")
    url = f"https://api.alternative.me/fng/?limit={days}&format=json"
    try:
        data = _fetch_json(url)
    except Exception as exc:
        print(f"  Error: {exc}")
        return 0

    if not isinstance(data, dict) or not data.get("data"):
        print("  No data returned.")
        return 0

    entries = data["data"]
    # Sort ascending by timestamp
    entries.sort(key=lambda e: int(e.get("timestamp", 0)))

    with out_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            try:
                record = {
                    "timestamp_ms":     int(entry["timestamp"]) * 1000,
                    "fng_value":        int(entry["value"]),
                    "fng_class":        entry.get("value_classification", ""),
                }
                fh.write(json.dumps(record) + "\n")
            except (KeyError, ValueError, TypeError):
                continue

    total = len(entries)
    print(f"  Done: {total} daily FNG records -> {out_path}")
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--months", type=int, default=24,
        help="Months of history to download for Coinbase/Kraken (default: 24)",
    )
    parser.add_argument(
        "--skip-coinbase", action="store_true",
        help="Skip Coinbase download (useful if API is rate-limiting)",
    )
    parser.add_argument(
        "--skip-kraken", action="store_true",
        help="Skip Kraken download",
    )
    parser.add_argument(
        "--skip-fng", action="store_true",
        help="Skip Fear & Greed Index download",
    )
    args = parser.parse_args()

    out_dir = Path("data/btc")
    out_dir.mkdir(parents=True, exist_ok=True)

    now_ms    = int(datetime.now(UTC).timestamp() * 1000)
    start_ms  = int((datetime.now(UTC) - timedelta(days=30 * args.months)).timestamp() * 1000)

    print(f"Fetching cross-exchange BTC data")
    print(f"  from   : {datetime.fromtimestamp(start_ms / 1000, UTC).strftime('%Y-%m-%d')}")
    print(f"  to     : {datetime.fromtimestamp(now_ms / 1000, UTC).strftime('%Y-%m-%d')}")

    n1 = n2 = n3 = 0

    if not args.skip_coinbase:
        n1 = fetch_coinbase_candles(start_ms, now_ms, out_dir / "ohlcv_5m_coinbase.jsonl")
    else:
        print("\n[1/3] Coinbase: skipped")

    if not args.skip_kraken:
        n2 = fetch_kraken_candles(start_ms, now_ms, out_dir / "ohlcv_5m_kraken.jsonl")
    else:
        print("\n[2/3] Kraken: skipped")

    if not args.skip_fng:
        n3 = fetch_fear_greed(out_dir / "fear_greed_daily.jsonl", days=max(args.months * 30, 365))
    else:
        print("\n[3/3] Fear & Greed: skipped")

    print(f"\nAll done: coinbase={n1}  kraken={n2}  fng={n3}")
    print("\nNext step — retrain model with cross-exchange features:")
    print("  python scripts/btc_train_exhaustive.py --interval 5m --data-dir data/btc")


if __name__ == "__main__":
    main()
