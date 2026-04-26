"""Score the production v4 CatBoost model on the 220k pure-BTC dataset.

Builds the full 32-feature schema expected by ``btc_15m_best_catboost.cbm``
and runs inference, then reports out-of-sample accuracy / Brier / PnL.

Coverage of features:
  ✓ 15m-derived from cached Binance klines (returns multi-TF, RSI 7/14/21,
    volatility 6c/12c/24c, BB10/BB20, MACD, volume_ratio, gap_open, rsi_1h)
  ✓ 5m, 1m derived (already in cache)
  ✓ funding_rate (already in cache)
  ✓ cyclic time features (sin/cos of hour, weekday)
  ✓ Coinbase lead-lag (cb_return_1c/3c, cb_bn_spread_1c/3c,
                       cb_vol_dominance, cb_momentum_lead)
    — fetched from Coinbase Exchange historical candles (6 years)
  ✗ ls_ratio_log, taker_ratio_log, oi_change_pct (futures stats)
    — Binance retains only ~30 days of 15m granularity; set to 0.
      Acceptable approximation for this validation run.

Output:
  data/btc15m/v4_scoring_report.json — accuracy / Brier / PnL stats
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

import catboost

ROOT = Path(__file__).resolve().parent.parent
KLINES_15M = ROOT / "data" / "btc15m" / "binance_15m_klines.jsonl"
COINBASE_15M = ROOT / "data" / "btc15m" / "coinbase_15m_klines.jsonl"
FUNDING_CACHE = ROOT / "data" / "btc15m" / "binance_funding_rate.jsonl"
VISION_METRICS = ROOT / "data" / "btc15m" / "binance_vision_metrics.jsonl"
PURE_FEATURES = ROOT / "data" / "btc15m" / "feature_rows_btc_pure.jsonl"
V4_MODEL = ROOT / "data" / "models" / "v4" / "btc_15m_best_catboost.cbm"
OUT_REPORT = ROOT / "data" / "btc15m" / "v4_scoring_report_v2.json"


def _http_json(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pmbot/0.1"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_coinbase_15m(start_dt: datetime, end_dt: datetime) -> dict[int, dict[str, float]]:
    """Coinbase Exchange historical 15m candles, paginated by start/end."""
    by_open: dict[int, dict[str, float]] = {}
    if COINBASE_15M.exists():
        with open(COINBASE_15M, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    by_open[int(row["open_time_ms"])] = row
                except (ValueError, TypeError, KeyError):
                    continue
        print(f"  cache: {len(by_open)} coinbase bars")

    INTERVAL_S = 900
    PAGE_S = 300 * INTERVAL_S
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    cursor_s = start_ms // 1000
    end_s = end_ms // 1000
    n = 0
    COINBASE_15M.parent.mkdir(parents=True, exist_ok=True)
    with open(COINBASE_15M, "a", encoding="utf-8") as f:
        while cursor_s < end_s:
            page_end_s = min(cursor_s + PAGE_S, end_s)
            iso_start = datetime.fromtimestamp(cursor_s, UTC).isoformat()
            iso_end = datetime.fromtimestamp(page_end_s, UTC).isoformat()
            url = (
                "https://api.exchange.coinbase.com/products/BTC-USD/candles"
                f"?granularity={INTERVAL_S}&start={iso_start}&end={iso_end}"
            )
            try:
                payload = _http_json(url)
            except (urllib.error.URLError, ValueError, TimeoutError) as e:
                print(f"    retry {e}", file=sys.stderr)
                time.sleep(1.0)
                continue
            if not isinstance(payload, list) or not payload:
                cursor_s = page_end_s
                continue
            # Coinbase returns: [time, low, high, open, close, volume] sorted DESC
            for c in payload:
                t_ms = int(c[0]) * 1000
                if t_ms in by_open:
                    continue
                row = {
                    "open_time_ms": t_ms,
                    "open": float(c[3]),
                    "high": float(c[2]),
                    "low": float(c[1]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                by_open[t_ms] = row
                f.write(json.dumps(row) + "\n")
            cursor_s = page_end_s
            n += 1
            if n % 25 == 0:
                print(
                    f"    coinbase {len(by_open)} bars  "
                    f"({datetime.fromtimestamp(cursor_s, UTC).date()})",
                    flush=True,
                )
            time.sleep(0.34)  # respect 3 req/s rate limit
    return by_open


def _wilder_rsi(closes: list[float], period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l <= 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _bb_pos(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return 0.5
    win = closes[-period:]
    mean = sum(win) / period
    std = math.sqrt(sum((x - mean) ** 2 for x in win) / period)
    if std <= 0:
        return 0.5
    return max(0.0, min(1.0, (closes[-1] - (mean - 2 * std)) / (4 * std)))


def _ema(closes: list[float], period: int) -> float:
    if not closes:
        return 0.0
    if len(closes) < period:
        return sum(closes) / len(closes)
    alpha = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = alpha * c + (1 - alpha) * ema
    return ema


def _macd(closes: list[float]) -> float:
    if len(closes) < 26:
        return 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    return ema12 - ema26


def _log_ret(c1: float, c0: float) -> float:
    if c0 <= 0 or c1 <= 0:
        return 0.0
    return math.log(c1 / c0)


def _vol(rets: list[float]) -> float:
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def _last_n_closed(
    by_open: dict[int, dict[str, float]],
    decision_ms: int,
    interval_ms: int,
    n: int,
) -> list[dict[str, float]]:
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
    out.reverse()
    return out


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


def load_vision_metrics() -> list[dict[str, float]]:
    """Load Binance Vision daily metrics, sorted by timestamp."""
    rows: list[dict[str, float]] = []
    if not VISION_METRICS.exists():
        return rows
    with open(VISION_METRICS, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    rows.sort(key=lambda r: r["timestamp_ms"])
    return rows


def metrics_at(metrics_sorted: list[dict[str, float]], decision_ms: int):
    """Find the latest metrics row <= decision_ms; return (current, prev24h)."""
    if not metrics_sorted or metrics_sorted[0]["timestamp_ms"] > decision_ms:
        return None, None
    lo, hi = 0, len(metrics_sorted) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if metrics_sorted[mid]["timestamp_ms"] <= decision_ms:
            lo = mid
        else:
            hi = mid - 1
    cur = metrics_sorted[lo]
    # 24h prev: ~288 rows back (5min granularity)
    prev_idx = max(lo - 288, 0)
    prev = metrics_sorted[prev_idx]
    return cur, prev


def compute_v4_features(
    decision_ms: int,
    by_15m: dict[int, dict[str, float]],
    by_cb_15m: dict[int, dict[str, float]],
    funding_sorted: list[dict[str, Any]],
    metrics_sorted: list[dict[str, float]] | None = None,
) -> list[float] | None:
    bars = _last_n_closed(by_15m, decision_ms, 900_000, 100)
    if len(bars) < 50:
        return None
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]

    # Multi-TF prev returns
    last = closes[-1]

    def ret_n(n: int) -> float:
        return _log_ret(last, closes[-1 - n]) if len(closes) > n else 0.0

    f_ret_1c = ret_n(1)
    f_ret_3c = ret_n(3)
    f_ret_6c = ret_n(6)
    f_ret_12c = ret_n(12)
    f_ret_24c = ret_n(24)
    f_ret_48c = ret_n(48)

    # RSI multi-period
    f_rsi_7 = _wilder_rsi(closes[-15:], 7)
    f_rsi_14 = _wilder_rsi(closes[-21:], 14)
    f_rsi_21 = _wilder_rsi(closes[-30:], 21)

    # Volume ratio (latest / 20-bar mean)
    if len(volumes) >= 20:
        vmean = sum(volumes[-20:]) / 20
        f_vol_ratio = volumes[-1] / vmean if vmean > 0 else 1.0
    else:
        f_vol_ratio = 1.0

    # Volatility multi-TF (std of log returns over n candles)
    def vol_n(n: int) -> float:
        if len(closes) < n + 1:
            return 0.0
        rets = [
            _log_ret(closes[-i], closes[-i - 1]) for i in range(1, n + 1)
        ]
        return _vol(rets)

    f_vol_6c = vol_n(6)
    f_vol_12c = vol_n(12)
    f_vol_24c = vol_n(24)

    # BB positions
    f_bb10 = _bb_pos(closes[-10:], 10)
    f_bb20 = _bb_pos(closes[-20:], 20)

    # MACD
    f_macd = _macd(closes[-30:])

    # Cyclic time features
    decision_dt = datetime.fromtimestamp(decision_ms / 1000, UTC)
    h = decision_dt.hour + decision_dt.minute / 60.0
    f_hour_sin = math.sin(2 * math.pi * h / 24)
    f_hour_cos = math.cos(2 * math.pi * h / 24)
    wd = decision_dt.weekday()
    f_wd_sin = math.sin(2 * math.pi * wd / 7)
    f_wd_cos = math.cos(2 * math.pi * wd / 7)

    # Coinbase lead-lag features
    cb_bars = _last_n_closed(by_cb_15m, decision_ms, 900_000, 5)
    if len(cb_bars) >= 4:
        cb_closes = [b["close"] for b in cb_bars]
        cb_vols = [b["volume"] for b in cb_bars]
        f_cb_ret_1c = _log_ret(cb_closes[-1], cb_closes[-2])
        f_cb_ret_3c = (
            _log_ret(cb_closes[-1], cb_closes[-4]) if len(cb_closes) >= 4 else 0.0
        )
        bn_last = closes[-1]
        bn_prev = closes[-2]
        bn_prev3 = closes[-4] if len(closes) >= 4 else closes[0]
        f_cb_bn_spread_1c = (cb_closes[-1] - bn_last) / bn_last
        f_cb_bn_spread_3c = (
            f_cb_ret_3c - _log_ret(bn_last, bn_prev3) if len(closes) >= 4 else 0.0
        )
        cb_total_vol = sum(cb_vols[-3:])
        bn_total_vol = sum(volumes[-3:])
        f_cb_vol_dom = (
            cb_total_vol / (cb_total_vol + bn_total_vol)
            if (cb_total_vol + bn_total_vol) > 0
            else 0.5
        )
        # Lead-lag: which exchange moved more in last 1 candle?
        cb_change = abs(f_cb_ret_1c)
        bn_change = abs(_log_ret(bn_last, bn_prev))
        if cb_change + bn_change > 0:
            f_cb_mom_lead = (cb_change - bn_change) / (cb_change + bn_change)
        else:
            f_cb_mom_lead = 0.0
    else:
        f_cb_ret_1c = 0.0
        f_cb_ret_3c = 0.0
        f_cb_bn_spread_1c = 0.0
        f_cb_bn_spread_3c = 0.0
        f_cb_vol_dom = 0.5
        f_cb_mom_lead = 0.0

    # Funding
    f_funding = latest_funding_at(funding_sorted, decision_ms)

    # Futures stats from Binance Vision archive
    f_ls_ratio = 0.0
    f_taker_ratio = 0.0
    f_oi_change = 0.0
    if metrics_sorted:
        cur_m, prev_m = metrics_at(metrics_sorted, decision_ms)
        if cur_m is not None:
            ls = cur_m.get("count_ls_ratio", 1.0)
            tk = cur_m.get("sum_taker_ls_vol_ratio", 1.0)
            f_ls_ratio = math.log(max(ls, 0.01))
            f_taker_ratio = math.log(max(tk, 0.01))
            if prev_m is not None and prev_m.get("sum_open_interest", 0) > 0:
                f_oi_change = (
                    (cur_m["sum_open_interest"] - prev_m["sum_open_interest"])
                    / prev_m["sum_open_interest"]
                )

    # Gap open: difference between current 15m open and previous close
    if len(bars) >= 2:
        cur_open = bars[-1]["open"]
        prev_close = bars[-2]["close"]
        f_gap_open = (cur_open - prev_close) / prev_close if prev_close > 0 else 0.0
    else:
        f_gap_open = 0.0

    # RSI 1h: aggregate every 4 closes into 1h
    if len(closes) >= 64:
        h_closes = [closes[-i] for i in range(64, 0, -4)]
        f_rsi_1h = _wilder_rsi(h_closes, 14)
    else:
        f_rsi_1h = 50.0

    # Order matters — match feature_names_ from the model.
    return [
        f_ret_1c, f_ret_3c, f_ret_6c, f_ret_12c, f_ret_24c, f_ret_48c,
        f_rsi_7, f_rsi_14, f_rsi_21,
        f_vol_ratio,
        f_vol_6c, f_vol_12c, f_vol_24c,
        f_bb10, f_bb20,
        f_macd,
        f_hour_sin, f_hour_cos,
        f_wd_sin, f_wd_cos,
        f_cb_ret_1c, f_cb_ret_3c, f_cb_bn_spread_1c, f_cb_bn_spread_3c,
        f_cb_vol_dom, f_cb_mom_lead,
        f_funding,
        f_ls_ratio, f_taker_ratio, f_oi_change,
        f_gap_open, f_rsi_1h,
    ]


def main() -> int:
    if not PURE_FEATURES.exists() or not V4_MODEL.exists():
        print("missing prerequisites", file=sys.stderr)
        return 1

    print("Loading 15m Binance klines from cache…")
    by_15m: dict[int, dict[str, float]] = {}
    with open(KLINES_15M, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            by_15m[int(row["open_time_ms"])] = row
    print(f"  {len(by_15m)} bars")

    print("Loading funding rates…")
    rates: list[dict[str, Any]] = []
    if FUNDING_CACHE.exists():
        with open(FUNDING_CACHE, encoding="utf-8") as f:
            for line in f:
                rates.append(json.loads(line))
    rates.sort(key=lambda r: r["fundingTime"])
    print(f"  {len(rates)} funding rates")

    earliest = datetime.fromtimestamp(min(by_15m) / 1000, UTC) - timedelta(hours=4)
    latest = datetime.fromtimestamp(max(by_15m) / 1000, UTC) + timedelta(hours=1)

    print(f"\nFetching Coinbase 15m candles {earliest.date()} -> {latest.date()}…")
    by_cb = fetch_coinbase_15m(earliest, latest)
    print(f"  {len(by_cb)} coinbase bars")

    print(f"\nLoading Binance Vision futures metrics…")
    metrics_sorted = load_vision_metrics()
    print(f"  {len(metrics_sorted)} metric rows")

    print(f"\nLoading v4 model…")
    model = catboost.CatBoostClassifier()
    model.load_model(str(V4_MODEL))
    print(f"  model: {len(model.feature_names_)} features")

    print(f"\nIterating slots and predicting…")
    rows = []
    with open(PURE_FEATURES, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    print(f"  {len(rows)} slots loaded")

    X = []
    y = []
    skipped = 0
    for r in rows:
        decision_iso = r["decision_timestamp_utc"]
        dt = datetime.fromisoformat(decision_iso.replace("Z", "+00:00"))
        decision_ms = int(dt.timestamp() * 1000)
        feats = compute_v4_features(decision_ms, by_15m, by_cb, rates, metrics_sorted)
        if feats is None:
            skipped += 1
            continue
        X.append(feats)
        y.append(r["label_yes"])
    print(f"  feature vectors built: {len(X)} (skipped {skipped})")

    print(f"\nRunning v4 inference on {len(X)} samples…")
    probs = model.predict_proba(X)
    yes_probs = [p[1] for p in probs]

    # Metrics
    n = len(y)
    correct = sum(1 for i in range(n) if (yes_probs[i] > 0.5) == bool(y[i]))
    accuracy = correct / n
    brier = sum((yes_probs[i] - y[i]) ** 2 for i in range(n)) / n
    log_loss = -sum(
        y[i] * math.log(max(yes_probs[i], 1e-15))
        + (1 - y[i]) * math.log(max(1 - yes_probs[i], 1e-15))
        for i in range(n)
    ) / n

    # Calibration: 10 bins
    bins = [[] for _ in range(10)]
    for i in range(n):
        b = min(int(yes_probs[i] * 10), 9)
        bins[b].append((yes_probs[i], y[i]))
    calib_err = 0.0
    for b in bins:
        if not b:
            continue
        mean_p = sum(p for p, _ in b) / len(b)
        mean_l = sum(l for _, l in b) / len(b)
        calib_err += abs(mean_p - mean_l) * len(b) / n

    # PnL backtest with v4's edge thresholds
    EDGE_BPS = 100  # 1% edge required
    FILL_PRICE = 0.475  # typical bot fill price
    SHARES_PER_TRADE = 1.0 / FILL_PRICE  # $1 stake → 2.1 shares
    SLIPPAGE_BPS = 50
    pnl = 0.0
    n_trades = 0
    wins = 0
    for i in range(n):
        p = yes_probs[i]
        market_implied = 0.5
        edge = abs(p - market_implied)
        if edge < EDGE_BPS / 10000:
            continue
        if p > 0.5:
            # bet YES at fill_price + slippage
            cost = (FILL_PRICE + SLIPPAGE_BPS / 10000) * SHARES_PER_TRADE
            payoff = SHARES_PER_TRADE if y[i] == 1 else 0.0
        else:
            cost = (FILL_PRICE + SLIPPAGE_BPS / 10000) * SHARES_PER_TRADE
            payoff = SHARES_PER_TRADE if y[i] == 0 else 0.0
        pnl += payoff - cost
        n_trades += 1
        if payoff > cost:
            wins += 1

    summary = {
        "model": "btc_15m_best_catboost.cbm v2.0.0",
        "samples": n,
        "skipped": skipped,
        "label_balance": sum(y) / n,
        "accuracy": accuracy,
        "brier_score": brier,
        "log_loss": log_loss,
        "calibration_error": calib_err,
        "pnl_backtest": {
            "edge_threshold_bps": EDGE_BPS,
            "fill_price_assumption": FILL_PRICE,
            "slippage_bps": SLIPPAGE_BPS,
            "n_trades": n_trades,
            "wins": wins,
            "win_rate": wins / n_trades if n_trades else 0,
            "total_pnl_per_dollar_stake": pnl,
            "pnl_per_trade": pnl / n_trades if n_trades else 0,
        },
        "notes": [
            "Full 32-feature schema: futures stats (ls_ratio, taker_ratio, "
            "oi_change) populated from Binance Vision archive (5min granularity).",
            "All features computed from Binance 15m + Coinbase 15m + funding + Vision metrics.",
        ],
    }

    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWROTE {OUT_REPORT}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
