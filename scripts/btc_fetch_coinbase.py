"""Download BTC-USD OHLCV from Coinbase Exchange API and save as JSONL.

Coinbase price discovery often leads Binance by 1-5 minutes — the CB-BN
lead-lag spread is a strong directional predictor for BTC 5m markets.

Supported granularities (seconds): 60, 300, 900, 3600, 21600, 86400
Maps to intervals: 1m, 5m, 15m, 1h, 6h, 1d

Usage:
    # 5m candles (default, 24 months)
    python scripts/btc_fetch_coinbase.py

    # 15m candles
    python scripts/btc_fetch_coinbase.py --interval 15m

    # 6 months only
    python scripts/btc_fetch_coinbase.py --months 6

Output file defaults to data/btc/coinbase_{interval}.jsonl
Same format as btc_fetch_ohlcv.py (open_time_ms key) for seamless merging.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
CANDLES_PER_REQUEST = 300   # Coinbase limit
THROTTLE_SEC = 0.4          # be conservative with Coinbase rate limits

# Granularity in seconds for each interval alias
_INTERVAL_GRANULARITY: dict[str, int] = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "1h":  3600,
    "6h":  21600,
    "1d":  86400,
}


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_candles(granularity: int, start_iso: str, end_iso: str) -> list[list]:
    """Fetch up to 300 candles from Coinbase between start and end (ISO 8601)."""
    params = {
        "granularity": str(granularity),
        "start": start_iso,
        "end":   end_iso,
    }
    url = f"{COINBASE_CANDLES_URL}?{urllib.parse.urlencode(params)}"

    if _HAS_REQUESTS:
        resp = _requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    else:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

    # Coinbase returns newest-first — reverse to chronological order
    return list(reversed(data))


def _candle_to_dict(candle: list, granularity: int) -> dict:
    """Convert Coinbase candle [time, low, high, open, close, volume] to dict.

    Output format mirrors btc_fetch_ohlcv.py so btc_train_v2.py can merge
    both datasets by open_time_ms.
    """
    open_time_sec = int(candle[0])
    return {
        "open_time_ms":  open_time_sec * 1000,
        "open":          float(candle[3]),
        "high":          float(candle[2]),
        "low":           float(candle[1]),
        "close":         float(candle[4]),
        "volume":        float(candle[5]),
        "close_time_ms": (open_time_sec + granularity - 1) * 1000,
        "source":        "coinbase",
    }


def fetch_all(
    granularity: int,
    months: int,
    out_path: Path,
) -> int:
    """Fetch all candles for the requested period and write to JSONL.

    Paginates backward from now using a sliding window of (300 × granularity) seconds.
    Returns the number of candles written.
    """
    window_sec = CANDLES_PER_REQUEST * granularity
    end_dt   = datetime.now(UTC)
    start_dt = end_dt - timedelta(days=months * 30)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    seen_ts: set[int] = set()
    total = 0
    max_ts_ms: int = 0

    # Load existing data to avoid re-downloading (resume support)
    if out_path.exists():
        with out_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ts = json.loads(line)["open_time_ms"]
                    seen_ts.add(ts)
                    if ts > max_ts_ms:
                        max_ts_ms = ts
        print(f"  Resuming: {len(seen_ts)} candles already on disk")

    # Jump to last known timestamp to avoid re-fetching already-covered windows
    if max_ts_ms > 0:
        resume_dt = datetime.fromtimestamp(max_ts_ms / 1000, UTC)
        if resume_dt > start_dt:
            print(f"  Fast-forwarding to {resume_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} (skipping already-fetched windows)")
            current_start = resume_dt
        else:
            current_start = start_dt
    else:
        current_start = start_dt
    with out_path.open("a", encoding="utf-8") as fh:
        while current_start < end_dt:
            current_end = min(current_start + timedelta(seconds=window_sec), end_dt)

            start_iso = current_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso   = current_end.strftime("%Y-%m-%dT%H:%M:%SZ")

            try:
                candles = _fetch_candles(granularity, start_iso, end_iso)
            except Exception as exc:
                print(f"  WARNING: fetch failed ({exc}), retrying in 2s ...")
                time.sleep(2.0)
                try:
                    candles = _fetch_candles(granularity, start_iso, end_iso)
                except Exception as exc2:
                    print(f"  ERROR: skipping window {start_iso} → {end_iso}: {exc2}")
                    current_start = current_end
                    continue

            new_in_batch = 0
            for raw in candles:
                rec = _candle_to_dict(raw, granularity)
                if rec["open_time_ms"] in seen_ts:
                    continue
                seen_ts.add(rec["open_time_ms"])
                fh.write(json.dumps(rec) + "\n")
                new_in_batch += 1

            total += new_in_batch
            current_start = current_end
            time.sleep(THROTTLE_SEC)

            pct = (current_start - start_dt) / (end_dt - start_dt) * 100
            print(f"  {total:>7} candles  [{pct:.1f}%]  window={start_iso[:10]}  batch={new_in_batch}", flush=True)

    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch BTC-USD OHLCV from Coinbase")
    parser.add_argument(
        "--interval", default="5m",
        choices=list(_INTERVAL_GRANULARITY),
        help="Candle interval (default: 5m)",
    )
    parser.add_argument(
        "--months", type=int, default=24,
        help="How many months of history to fetch (default: 24)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output JSONL path (default: data/btc/coinbase_{interval}.jsonl)",
    )
    args = parser.parse_args()

    granularity = _INTERVAL_GRANULARITY[args.interval]
    out_path = Path(args.out) if args.out else Path(f"data/btc/coinbase_{args.interval}.jsonl")

    print(f"Fetching Coinbase BTC-USD {args.interval} candles — {args.months} months → {out_path}")
    n = fetch_all(granularity=granularity, months=args.months, out_path=out_path)

    # Sort output file by open_time_ms (append mode may create gaps)
    print(f"Sorting {out_path} by timestamp ...")
    candles: list[dict] = []
    with out_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                candles.append(json.loads(line))
    candles.sort(key=lambda c: c["open_time_ms"])
    with out_path.open("w", encoding="utf-8") as fh:
        for c in candles:
            fh.write(json.dumps(c) + "\n")

    print(f"Done. {len(candles)} candles in {out_path}")
    if candles:
        t0 = datetime.fromtimestamp(candles[0]["open_time_ms"] / 1000, UTC).strftime("%Y-%m-%d")
        t1 = datetime.fromtimestamp(candles[-1]["open_time_ms"] / 1000, UTC).strftime("%Y-%m-%d")
        print(f"  Range: {t0} → {t1}")


if __name__ == "__main__":
    main()
