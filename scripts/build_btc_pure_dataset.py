"""Pure BTC 15m direction-prediction dataset.

The "BTC Up/Down 15m" prediction task is mathematically equivalent to
predicting whether the next 15-minute candle on BTCUSDT will close
green: ``close >= open``.  Polymarket's market resolution merely
attests this fact — but the underlying ground truth lives on the
exchange itself, not on the prediction market.

This script bypasses Polymarket entirely and treats every closed
Binance 15m candle as a training sample:

    decision_ts  = candle.open_time + 15s   (when the bot would have
                                              filled live)
    label_yes    = 1 if candle.close >= candle.open else 0

Feature engineering uses ONLY data available before candle.open_time
(no look-ahead).  We use the same v2 schema (10 features) so we can
directly compare against the Polymarket-labelled version.

Output: data/btc15m/feature_rows_btc_pure.jsonl
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
KLINES_15M = ROOT / "data" / "btc15m" / "binance_15m_klines.jsonl"
KLINES_5M = ROOT / "data" / "btc15m" / "binance_5m_klines.jsonl"
KLINES_1M = ROOT / "data" / "btc15m" / "binance_1m_klines.jsonl"
FUNDING_CACHE = ROOT / "data" / "btc15m" / "binance_funding_rate.jsonl"
OUT = ROOT / "data" / "btc15m" / "feature_rows_btc_pure.jsonl"

# Backfill window: BTCUSDT futures funding rate history starts Sep 2019,
# so for clean perpetuals features we only go back to Jan 2020.
BACKFILL_START = datetime(2020, 1, 1, tzinfo=UTC)


def _http_json(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pmbot/0.1"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_klines_extended(
    symbol: str, interval: str, start_dt: datetime, end_dt: datetime, cache: Path
) -> dict[int, dict[str, float]]:
    """Fetch klines, extending the cache to cover [start_dt, end_dt]."""
    by_open: dict[int, dict[str, float]] = {}
    if cache.exists():
        with open(cache, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    by_open[int(row["open_time_ms"])] = row
                except (ValueError, TypeError, KeyError):
                    continue
        print(f"  cache: {len(by_open)} {interval} bars")

    interval_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000}[interval]
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    # Determine fetch ranges (left of cache and right of cache).
    fetch_ranges: list[tuple[int, int]] = []
    if not by_open:
        fetch_ranges.append((start_ms, end_ms))
    else:
        cache_min = min(by_open)
        cache_max = max(by_open)
        if start_ms < cache_min:
            fetch_ranges.append((start_ms, cache_min))
        if end_ms > cache_max + interval_ms:
            fetch_ranges.append((cache_max + interval_ms, end_ms))

    if not fetch_ranges:
        return by_open

    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "a", encoding="utf-8") as f:
        for fr_start, fr_end in fetch_ranges:
            cursor_ms = fr_start
            print(
                f"  fetching {interval} from "
                f"{datetime.fromtimestamp(cursor_ms/1000, UTC).date()} -> "
                f"{datetime.fromtimestamp(fr_end/1000, UTC).date()}",
                flush=True,
            )
            n = 0
            while cursor_ms < fr_end:
                url = (
                    "https://api.binance.com/api/v3/klines"
                    f"?symbol={symbol}&interval={interval}"
                    f"&limit=1000&startTime={cursor_ms}"
                )
                try:
                    payload = _http_json(url)
                except (urllib.error.URLError, ValueError, TimeoutError) as e:
                    print(f"    retry {e}", file=sys.stderr)
                    time.sleep(1.0)
                    continue
                if not isinstance(payload, list) or not payload:
                    break
                for k in payload:
                    row = {
                        "open_time_ms": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                    if row["open_time_ms"] not in by_open:
                        by_open[row["open_time_ms"]] = row
                        f.write(json.dumps(row) + "\n")
                last_open = int(payload[-1][0])
                cursor_ms = last_open + interval_ms
                n += 1
                if n % 20 == 0:
                    print(
                        f"    {len(by_open)} bars total ({datetime.fromtimestamp(last_open/1000, UTC).date()})",
                        flush=True,
                    )
                time.sleep(0.04)
    return by_open


def fetch_funding_rates_extended(start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    if FUNDING_CACHE.exists():
        with open(FUNDING_CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    rates.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
        print(f"  funding cache: {len(rates)} rates")

    have_times = {int(r["fundingTime"]) for r in rates}
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    fetch_ranges: list[tuple[int, int]] = []
    if not rates:
        fetch_ranges.append((start_ms, end_ms))
    else:
        cache_min = min(have_times)
        cache_max = max(have_times)
        if start_ms < cache_min:
            fetch_ranges.append((start_ms, cache_min))
        if end_ms > cache_max + 8 * 3600 * 1000:
            fetch_ranges.append((cache_max + 1, end_ms))

    FUNDING_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(FUNDING_CACHE, "a", encoding="utf-8") as f:
        for fr_start, fr_end in fetch_ranges:
            cursor_ms = fr_start
            print(
                f"  funding from "
                f"{datetime.fromtimestamp(cursor_ms/1000, UTC).date()} -> "
                f"{datetime.fromtimestamp(fr_end/1000, UTC).date()}",
                flush=True,
            )
            while cursor_ms < fr_end:
                url = (
                    "https://fapi.binance.com/fapi/v1/fundingRate"
                    f"?symbol=BTCUSDT&limit=1000&startTime={cursor_ms}"
                )
                try:
                    payload = _http_json(url)
                except (urllib.error.URLError, ValueError, TimeoutError) as e:
                    print(f"    retry funding: {e}", file=sys.stderr)
                    time.sleep(1.0)
                    continue
                if not isinstance(payload, list) or not payload:
                    break
                for r in payload:
                    t = int(r.get("fundingTime", 0))
                    if t == 0 or t in have_times:
                        continue
                    row = {"fundingTime": t, "fundingRate": float(r.get("fundingRate", 0.0))}
                    rates.append(row)
                    have_times.add(t)
                    f.write(json.dumps(row) + "\n")
                cursor_ms = int(payload[-1]["fundingTime"]) + 1
                time.sleep(0.05)
    rates.sort(key=lambda r: r["fundingTime"])
    return rates


def latest_funding_at(rates_sorted: list[dict[str, Any]], decision_ms: int) -> float:
    if not rates_sorted or rates_sorted[0]["fundingTime"] > decision_ms:
        return 0.0
    lo, hi = 0, len(rates_sorted) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if rates_sorted[mid]["fundingTime"] <= decision_ms:
            lo = mid
        else:
            hi = mid - 1
    return rates_sorted[lo]["fundingRate"]


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
    std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    if std <= 0:
        return 0.5
    upper = mean + 2 * std
    lower = mean - 2 * std
    return max(0.0, min(1.0, (closes[-1] - lower) / (upper - lower)))


def _log_return(c1: float, c0: float) -> float:
    if c0 <= 0 or c1 <= 0:
        return 0.0
    return math.log(c1 / c0)


def _atr(rows: list[dict[str, float]], period: int = 14) -> float | None:
    if len(rows) < period + 1:
        return None
    trs = [
        max(rows[i]["high"] - rows[i]["low"],
            abs(rows[i]["high"] - rows[i - 1]["close"]),
            abs(rows[i]["low"] - rows[i - 1]["close"]))
        for i in range(1, len(rows))
    ]
    return sum(trs[-period:]) / period


def _last_n_closed(
    by_open: dict[int, dict[str, float]],
    decision_ms: int,
    interval_ms: int,
    n: int,
) -> list[dict[str, float]] | None:
    cutoff = decision_ms - interval_ms
    cursor = cutoff - cutoff % interval_ms
    out: list[dict[str, float]] = []
    for _ in range(n + 5):
        row = by_open.get(cursor)
        if row is not None:
            out.append(row)
        cursor -= interval_ms
        if len(out) >= n:
            break
    if len(out) < n:
        return None
    out.reverse()
    return out


def compute_features(
    decision_ms: int,
    by_15m: dict[int, dict[str, float]],
    by_5m: dict[int, dict[str, float]],
    by_1m: dict[int, dict[str, float]],
    funding_sorted: list[dict[str, Any]],
) -> dict[str, float] | None:
    bars_15m = _last_n_closed(by_15m, decision_ms, 900_000, 96)
    if bars_15m is None or len(bars_15m) < 21:
        return None
    bars_5m = _last_n_closed(by_5m, decision_ms, 300_000, 13)
    bars_1m = _last_n_closed(by_1m, decision_ms, 60_000, 6)
    if bars_5m is None or bars_1m is None:
        return None

    closes_15m = [b["close"] for b in bars_15m]
    rsi14 = _wilder_rsi(closes_15m[-21:], 14)
    bb20 = _bb_position(closes_15m[-20:], 20)
    if rsi14 is None or bb20 is None:
        return None
    last_15m, prev_15m = closes_15m[-1], closes_15m[-2]
    prev3_15m = closes_15m[-4] if len(closes_15m) >= 4 else closes_15m[0]
    f_prev_1c = _log_return(last_15m, prev_15m)
    f_prev_3c = _log_return(last_15m, prev3_15m)

    closes_5m = [b["close"] for b in bars_5m]
    log_rets_5m = [
        _log_return(closes_5m[i + 1], closes_5m[i])
        for i in range(len(closes_5m) - 1)
    ]
    f_5m_mom = sum(log_rets_5m)
    if len(log_rets_5m) >= 2:
        mu = sum(log_rets_5m) / len(log_rets_5m)
        var = sum((r - mu) ** 2 for r in log_rets_5m) / (len(log_rets_5m) - 1)
        f_5m_vol = math.sqrt(var)
    else:
        f_5m_vol = 0.0

    closes_1m = [b["close"] for b in bars_1m]
    log_rets_1m = [
        _log_return(closes_1m[i + 1], closes_1m[i])
        for i in range(len(closes_1m) - 1)
    ]
    f_1m_mom = sum(log_rets_1m[-5:])

    volumes = [b["volume"] for b in bars_15m[-96:]]
    if len(volumes) >= 8:
        mu_v = sum(volumes) / len(volumes)
        std_v = math.sqrt(sum((v - mu_v) ** 2 for v in volumes) / max(len(volumes) - 1, 1))
        f_vol_z = (volumes[-1] - mu_v) / std_v if std_v > 0 else 0.0
    else:
        f_vol_z = 0.0

    f_atr14 = _atr(bars_15m[-30:], 14) or 0.0
    f_fund = latest_funding_at(funding_sorted, decision_ms)

    return {
        "f_btc_rsi_14": rsi14,
        "f_btc_bb_position_20": bb20,
        "f_btc_prev_return_1c": f_prev_1c,
        "f_btc_prev_return_3c": f_prev_3c,
        "f_btc_funding_rate": f_fund,
        "f_btc_5m_momentum_1h": f_5m_mom,
        "f_btc_5m_realized_vol_1h": f_5m_vol,
        "f_btc_1m_momentum_5m": f_1m_mom,
        "f_btc_volume_zscore_24h": f_vol_z,
        "f_btc_atr_14_15m": f_atr14,
    }


def main() -> int:
    end_dt = datetime.now(UTC) - timedelta(minutes=30)
    print(f"Range: {BACKFILL_START.isoformat()} -> {end_dt.isoformat()}")
    print(f"Expected slots (15m bars): ~{int((end_dt - BACKFILL_START).total_seconds()/900)}")

    print("\nLoading 15m klines...")
    by_15m = fetch_klines_extended("BTCUSDT", "15m", BACKFILL_START, end_dt, KLINES_15M)
    print(f"  15m total: {len(by_15m)}")

    print("\nLoading 5m klines...")
    by_5m = fetch_klines_extended("BTCUSDT", "5m", BACKFILL_START, end_dt, KLINES_5M)
    print(f"  5m total: {len(by_5m)}")

    print("\nLoading 1m klines...")
    by_1m = fetch_klines_extended("BTCUSDT", "1m", BACKFILL_START, end_dt, KLINES_1M)
    print(f"  1m total: {len(by_1m)}")

    print("\nLoading funding rates...")
    funding = fetch_funding_rates_extended(BACKFILL_START, end_dt)
    print(f"  funding rates: {len(funding)}")

    print("\nIterating 15m candles to build training samples...")
    sorted_open_times = sorted(by_15m.keys())
    rows: list[dict[str, Any]] = []
    skipped = 0
    for open_ms in sorted_open_times:
        candle = by_15m[open_ms]
        # Decision time = open + 15s (matches live bot fill behaviour)
        decision_ms = open_ms + 15_000
        # Label: did the candle close green?
        label_yes = 1 if candle["close"] >= candle["open"] else 0
        feats = compute_features(decision_ms, by_15m, by_5m, by_1m, funding)
        if feats is None:
            skipped += 1
            continue
        decision_iso = (
            datetime.fromtimestamp(decision_ms / 1000, UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        rows.append(
            {
                "row_id": f"btc15m@{open_ms}",
                "market_id": f"btc15m@{open_ms}",
                "event_id": "",
                "category": "Crypto",
                "decision_timestamp_utc": decision_iso,
                "label_yes": label_yes,
                "data_quality_flags": [],
                "feature_schema_version": "btc-pure-v3",
                **feats,
            }
        )

    print(f"  rows built: {len(rows)} (skipped {skipped})")
    pos = sum(r["label_yes"] for r in rows)
    print(
        f"  label balance: YES={pos} ({pos/len(rows)*100:.2f}%) "
        f"NO={len(rows)-pos} ({(len(rows)-pos)/len(rows)*100:.2f}%)"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nWROTE {OUT}  ({len(rows)} rows, 10 features, 6+ years of BTC history)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
