"""Extended BTC Up/Down 15m feature dataset (v2 schema).

Adds 6 new features on top of the v1 four:
  f_btc_funding_rate          — Binance perpetual funding rate (snap at decision_ts)
  f_btc_5m_momentum_1h        — sum of last 12 5-min log returns (1h drift)
  f_btc_5m_realized_vol_1h    — std of last 12 5-min log returns
  f_btc_1m_momentum_5m        — sum of last 5 1-min log returns (very recent push)
  f_btc_volume_zscore_24h     — latest 15m volume z-scored over last 96 candles
  f_btc_atr_14_15m            — average true range over last 14 15m bars

Output: data/btc15m/feature_rows_btc_v2.jsonl (10 features per row)
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
RAW_EVENTS = ROOT / "data" / "btc15m" / "events_raw.jsonl"
KLINES_15M = ROOT / "data" / "btc15m" / "binance_15m_klines.jsonl"
KLINES_5M = ROOT / "data" / "btc15m" / "binance_5m_klines.jsonl"
KLINES_1M = ROOT / "data" / "btc15m" / "binance_1m_klines.jsonl"
FUNDING_CACHE = ROOT / "data" / "btc15m" / "binance_funding_rate.jsonl"
OUT = ROOT / "data" / "btc15m" / "feature_rows_btc_v2.jsonl"


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)


def _http_json(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pmbot/0.1"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_klines(
    symbol: str, interval: str, start_dt: datetime, end_dt: datetime, cache: Path
) -> dict[int, dict[str, float]]:
    by_open: dict[int, dict[str, float]] = {}
    if cache.exists():
        with open(cache, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    by_open[int(row["open_time_ms"])] = row
                except (ValueError, TypeError, KeyError):
                    continue
        print(f"  cache hit: {len(by_open)} {interval} bars")

    if by_open:
        cache_min = min(by_open) / 1000
        cache_max = max(by_open) / 1000
        if cache_min <= start_dt.timestamp() and cache_max >= end_dt.timestamp() - 60:
            return by_open

    interval_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000}[interval]
    cursor_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    n = 0
    cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "a", encoding="utf-8") as f:
        while cursor_ms < end_ms:
            url = (
                "https://api.binance.com/api/v3/klines"
                f"?symbol={symbol}&interval={interval}"
                f"&limit=1000&startTime={cursor_ms}"
            )
            try:
                payload = _http_json(url)
            except (urllib.error.URLError, ValueError, TimeoutError) as e:
                print(f"  retry {e}", file=sys.stderr)
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
            if n % 10 == 0:
                print(
                    f"  {interval}: {len(by_open)} bars  "
                    f"({datetime.fromtimestamp(last_open/1000, UTC).isoformat()})",
                    flush=True,
                )
            time.sleep(0.05)
    return by_open


def fetch_funding_rates(start_dt: datetime, end_dt: datetime) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    if FUNDING_CACHE.exists():
        with open(FUNDING_CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    rates.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
        print(f"  funding cache hit: {len(rates)} rates")
        if rates:
            min_t = min(r["fundingTime"] for r in rates) / 1000
            max_t = max(r["fundingTime"] for r in rates) / 1000
            if (
                min_t <= start_dt.timestamp()
                and max_t >= end_dt.timestamp() - 8 * 3600
            ):
                return rates

    cursor_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    have_times = {int(r["fundingTime"]) for r in rates}
    FUNDING_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(FUNDING_CACHE, "a", encoding="utf-8") as f:
        while cursor_ms < end_ms:
            url = (
                "https://fapi.binance.com/fapi/v1/fundingRate"
                f"?symbol=BTCUSDT&limit=1000&startTime={cursor_ms}"
            )
            try:
                payload = _http_json(url)
            except (urllib.error.URLError, ValueError, TimeoutError) as e:
                print(f"  retry funding: {e}", file=sys.stderr)
                time.sleep(1.0)
                continue
            if not isinstance(payload, list) or not payload:
                break
            for r in payload:
                t = int(r.get("fundingTime", 0))
                if t == 0 or t in have_times:
                    continue
                row = {
                    "fundingTime": t,
                    "fundingRate": float(r.get("fundingRate", 0.0)),
                }
                rates.append(row)
                have_times.add(t)
                f.write(json.dumps(row) + "\n")
            cursor_ms = int(payload[-1]["fundingTime"]) + 1
            time.sleep(0.05)
    rates.sort(key=lambda r: r["fundingTime"])
    return rates


def latest_funding_at(rates_sorted: list[dict[str, Any]], decision_ms: int) -> float:
    """Binary search the latest funding rate <= decision_ms."""
    if not rates_sorted:
        return 0.0
    lo, hi = 0, len(rates_sorted) - 1
    if rates_sorted[0]["fundingTime"] > decision_ms:
        return 0.0
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


def _atr(rows: list[dict[str, float]], period: int = 14) -> float | None:
    if len(rows) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(rows)):
        h = rows[i]["high"]
        l = rows[i]["low"]
        c_prev = rows[i - 1]["close"]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
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


def compute_features_v2(
    decision_ts: datetime,
    by_15m: dict[int, dict[str, float]],
    by_5m: dict[int, dict[str, float]],
    by_1m: dict[int, dict[str, float]],
    funding_sorted: list[dict[str, Any]],
) -> dict[str, float] | None:
    decision_ms = int(decision_ts.timestamp() * 1000)
    bars_15m = _last_n_closed(by_15m, decision_ms, 900_000, 96)
    if bars_15m is None or len(bars_15m) < 21:
        return None
    bars_5m = _last_n_closed(by_5m, decision_ms, 300_000, 13)
    bars_1m = _last_n_closed(by_1m, decision_ms, 60_000, 6)
    if bars_5m is None or bars_1m is None:
        return None

    # v1 features (4)
    closes_15m = [b["close"] for b in bars_15m]
    rsi14 = _wilder_rsi(closes_15m[-21:], 14)
    bb20 = _bb_position(closes_15m[-20:], 20)
    if rsi14 is None or bb20 is None:
        return None
    last_15m = closes_15m[-1]
    prev_15m = closes_15m[-2]
    prev3_15m = closes_15m[-4] if len(closes_15m) >= 4 else closes_15m[0]
    f_prev_1c = _log_return(last_15m, prev_15m)
    f_prev_3c = _log_return(last_15m, prev3_15m)

    # 5m momentum + realized vol over 1h
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

    # 1m momentum last 5
    closes_1m = [b["close"] for b in bars_1m]
    log_rets_1m = [
        _log_return(closes_1m[i + 1], closes_1m[i])
        for i in range(len(closes_1m) - 1)
    ]
    f_1m_mom = sum(log_rets_1m[-5:])

    # volume zscore over last 96 15m bars
    volumes = [b["volume"] for b in bars_15m[-96:]]
    if len(volumes) >= 8:
        mu_v = sum(volumes) / len(volumes)
        var_v = sum((v - mu_v) ** 2 for v in volumes) / max(len(volumes) - 1, 1)
        std_v = math.sqrt(var_v)
        f_vol_z = (volumes[-1] - mu_v) / std_v if std_v > 0 else 0.0
    else:
        f_vol_z = 0.0

    # ATR 14 over 15m
    f_atr14 = _atr(bars_15m[-30:], 14) or 0.0

    # funding rate at decision time
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


def derive_slots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            yp = float(prices[0])
            np_ = float(prices[1])
        except (ValueError, TypeError):
            continue
        if not ((yp > 0.95 and np_ < 0.05) or (np_ > 0.95 and yp < 0.05)):
            continue
        decision_ts = (end_dt - timedelta(minutes=15)) + timedelta(seconds=15)
        slots.append(
            {
                "market_id": str(m.get("id") or m.get("conditionId") or ""),
                "event_id": str(ev.get("id") or ""),
                "decision_timestamp_utc": decision_ts.isoformat().replace(
                    "+00:00", "Z"
                ),
                "label_yes": 1 if yp > 0.5 else 0,
                "title": m.get("question") or "",
            }
        )
    slots.sort(key=lambda s: s["decision_timestamp_utc"])
    return slots


def main() -> int:
    if not RAW_EVENTS.exists():
        print(f"!! missing {RAW_EVENTS}", file=sys.stderr)
        return 1

    print("Loading events...")
    events = []
    with open(RAW_EVENTS, encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    print(f"  events: {len(events)}")
    slots = derive_slots(events)
    print(f"  binary-resolved slots: {len(slots)}")
    if not slots:
        return 1

    earliest = _utc(slots[0]["decision_timestamp_utc"]) - timedelta(hours=8)
    latest = _utc(slots[-1]["decision_timestamp_utc"]) + timedelta(hours=1)
    print(
        f"  range: {earliest.isoformat()} -> {latest.isoformat()}  "
        f"({(latest - earliest).days} days)"
    )

    print("\nLoading 15m klines...")
    by_15m = fetch_klines("BTCUSDT", "15m", earliest, latest, KLINES_15M)
    print(f"  15m: {len(by_15m)}")

    print("\nLoading 5m klines...")
    by_5m = fetch_klines("BTCUSDT", "5m", earliest, latest, KLINES_5M)
    print(f"  5m: {len(by_5m)}")

    print("\nLoading 1m klines...")
    by_1m = fetch_klines("BTCUSDT", "1m", earliest, latest, KLINES_1M)
    print(f"  1m: {len(by_1m)}")

    print("\nLoading funding rates (perpetuals)...")
    funding = fetch_funding_rates(earliest, latest)
    print(f"  funding rates: {len(funding)}")

    print("\nComputing features per slot...")
    rows: list[dict[str, Any]] = []
    skipped = 0
    for s in slots:
        decision_ts = _utc(s["decision_timestamp_utc"])
        feats = compute_features_v2(decision_ts, by_15m, by_5m, by_1m, funding)
        if feats is None:
            skipped += 1
            continue
        rows.append(
            {
                "row_id": f"{s['market_id']}@{s['decision_timestamp_utc']}",
                "market_id": s["market_id"],
                "event_id": s["event_id"],
                "category": "Crypto",
                "decision_timestamp_utc": s["decision_timestamp_utc"],
                "label_yes": s["label_yes"],
                "data_quality_flags": [],
                "feature_schema_version": "btc-v3-15m",
                **feats,
            }
        )
    print(f"  rows built: {len(rows)} (skipped {skipped})")

    pos = sum(r["label_yes"] for r in rows)
    print(
        f"  label balance: YES={pos} ({pos/len(rows)*100:.1f}%) "
        f"NO={len(rows)-pos} ({(len(rows)-pos)/len(rows)*100:.1f}%)"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nWROTE {OUT}  ({len(rows)} rows, 10 features)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
