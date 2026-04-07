"""Download BTC/USDT 5-minute OHLCV from Binance and save as JSONL.

Usage:
    python scripts/btc_fetch_ohlcv.py [--months 24] [--out data/btc/ohlcv_5m.jsonl]

Downloads up to `months` months of 5-minute candles (1000 candles per request,
~3.5 days each, throttled at 0.2s between requests).
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
CANDLES_PER_REQUEST = 1000
THROTTLE_SEC = 0.25


def _fetch_klines(start_ms: int, end_ms: int | None = None) -> list[list]:
    params = f"symbol={SYMBOL}&interval={INTERVAL}&limit={CANDLES_PER_REQUEST}&startTime={start_ms}"
    if end_ms is not None:
        params += f"&endTime={end_ms}"
    url = f"{BINANCE_KLINES_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _candle_to_dict(candle: list) -> dict:
    return {
        "open_time_ms": int(candle[0]),
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
        "close_time_ms": int(candle[6]),
        "quote_volume": float(candle[7]),
        "trades": int(candle[8]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=24, help="Months of history to download (default: 24)")
    parser.add_argument("--out", default="data/btc/ohlcv_5m.jsonl", help="Output JSONL path")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=30 * args.months)).timestamp() * 1000)

    total_written = 0
    cursor_ms = start_ms

    print(f"Downloading BTC/USDT 5m from {datetime.fromtimestamp(start_ms/1000, UTC).date()} ...")

    with out_path.open("w", encoding="utf-8") as fh:
        while cursor_ms < now_ms:
            try:
                candles = _fetch_klines(cursor_ms, end_ms=now_ms)
            except Exception as exc:
                print(f"  Error at {cursor_ms}: {exc}. Retrying in 2s...")
                time.sleep(2)
                continue

            if not candles:
                break

            for raw in candles:
                fh.write(json.dumps(_candle_to_dict(raw)))
                fh.write("\n")

            total_written += len(candles)
            last_close_ms = int(candles[-1][6])
            cursor_ms = last_close_ms + 1

            pct = min((cursor_ms - start_ms) / (now_ms - start_ms) * 100, 100)
            print(f"  {total_written:>7} candles  {pct:.1f}%  last={datetime.fromtimestamp(last_close_ms/1000, UTC).strftime('%Y-%m-%d')}")

            if len(candles) < CANDLES_PER_REQUEST:
                break

            time.sleep(THROTTLE_SEC)

    print(f"\nDone. {total_written} candles saved to {out_path}")


if __name__ == "__main__":
    main()
