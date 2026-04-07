"""Download BTC/USDT OHLCV from Binance and save as JSONL.

Supports any Binance kline interval: 1m, 3m, 5m, 15m, 30m, 1h, 4h, 1d, ...

Usage:
    # 5m candles (default, 24 months)
    python scripts/btc_fetch_ohlcv.py

    # 15m candles
    python scripts/btc_fetch_ohlcv.py --interval 15m

    # 1h candles, 36 months
    python scripts/btc_fetch_ohlcv.py --interval 1h --months 36

    # custom output path
    python scripts/btc_fetch_ohlcv.py --interval 15m --out data/btc/ohlcv_15m.jsonl

Output file defaults to data/btc/ohlcv_{interval}.jsonl
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
CANDLES_PER_REQUEST = 1000
THROTTLE_SEC = 0.25

# Approximate candle duration in seconds (used for progress estimate only)
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}


def _fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int | None = None) -> list[list]:
    params = f"symbol={symbol}&interval={interval}&limit={CANDLES_PER_REQUEST}&startTime={start_ms}"
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", default="5m",
                        help="Binance kline interval (default: 5m). Examples: 1m 3m 5m 15m 30m 1h 4h 1d")
    parser.add_argument("--months", type=int, default=24,
                        help="Months of history to download (default: 24)")
    parser.add_argument("--symbol", default=SYMBOL,
                        help=f"Trading pair symbol (default: {SYMBOL})")
    parser.add_argument("--out", default="",
                        help="Output JSONL path (default: data/btc/ohlcv_{interval}.jsonl)")
    args = parser.parse_args()

    interval = args.interval.lower().strip()
    out_path = Path(args.out) if args.out else Path(f"data/btc/ohlcv_{interval}.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=30 * args.months)).timestamp() * 1000)

    total_written = 0
    cursor_ms = start_ms

    print(f"Downloading {args.symbol} {interval} candles")
    print(f"  from  : {datetime.fromtimestamp(start_ms/1000, UTC).strftime('%Y-%m-%d')}")
    print(f"  to    : {datetime.fromtimestamp(now_ms/1000, UTC).strftime('%Y-%m-%d')}")
    print(f"  output: {out_path}")
    print()

    with out_path.open("w", encoding="utf-8") as fh:
        while cursor_ms < now_ms:
            try:
                candles = _fetch_klines(args.symbol, interval, cursor_ms, end_ms=now_ms)
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
            last_dt = datetime.fromtimestamp(last_close_ms / 1000, UTC).strftime("%Y-%m-%d")
            print(f"  {total_written:>8} candles  {pct:5.1f}%  last={last_dt}")

            if len(candles) < CANDLES_PER_REQUEST:
                break

            time.sleep(THROTTLE_SEC)

    print(f"\nDone. {total_written:,} candles saved to {out_path}")


if __name__ == "__main__":
    main()
