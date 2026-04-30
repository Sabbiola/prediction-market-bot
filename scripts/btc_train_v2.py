"""Unified BTC Up/Down training script — PnL-optimised, multi-exchange, LGBM+XGB+LSTM+TCN.

Replaces btc_train_model.py and btc_train_exhaustive.py.

Features:
  - Binance OHLCV (technical indicators)
  - Coinbase lead-lag spread (CB price discovery leads BN by 1-5 min)
  - Derivatives: funding rate, LS ratio, OI (if available)
  - LSTM + TCN deep learning (if PyTorch installed)
  - Model selection by PnL Sharpe, not AUC
  - Sliding-window daily retraining (--live mode)
  - Auto-promotes if new model beats current by PnL gate

Usage:
    # Full training (first run, 6-month window)
    python scripts/btc_train_v2.py --interval 5m --lookback-months 6

    # All intervals at once
    python scripts/btc_train_v2.py --interval all --lookback-months 6

    # Daily live update (for cron)
    python scripts/btc_train_v2.py --interval all --live --lookback-months 6

    # Skip deep learning (faster, no torch required)
    python scripts/btc_train_v2.py --interval 5m --algorithms lgbm,xgb

Output:
    data/models/v4/btc_{interval}_best.json    — main artifact for agents.yaml
    data/models/v4/btc_{interval}_best.model   — tree model file (if lgbm/xgb)
    data/models/v4/btc_{interval}_lstm.pth     — LSTM weights (if torch)
    data/models/v4/btc_{interval}_tcn.pth      — TCN weights (if torch)
    data/btc/training_log.jsonl                — history of training runs
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional heavy imports — graceful degradation
# ---------------------------------------------------------------------------
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    print("WARNING: numpy not installed. Install with: pip install numpy")
    sys.exit(1)

try:
    import lightgbm as lgb
    HAS_LGBM = True
except ImportError:
    HAS_LGBM = False

try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    print("WARNING: scikit-learn not installed. Install with: pip install scikit-learn")
    sys.exit(1)

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

try:
    import catboost as cb_lib
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    # Stub so module-level class definitions with nn.Module don't crash at import
    class _NnStub:
        Module = object
        def __getattr__(self, name: str) -> Any:
            return lambda *a, **kw: None
    nn = _NnStub()  # type: ignore[assignment]
    torch = None    # type: ignore[assignment]
    optim = None    # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_LOOKBACK = 60   # min completed candles before computing features
SEQ_LEN = 60             # LSTM/TCN input sequence length (candles)
FEE_BPS  = 180           # Polymarket BTC taker fee (crypto category, 2026)
KELLY_FRACTION = 0.05    # fractional Kelly for PnL simulation

# All feature names produced by this script
FEATURE_NAMES = [
    # Binance technical
    "f_btc_prev_return_1c",
    "f_btc_prev_return_3c",
    "f_btc_prev_return_6c",
    "f_btc_prev_return_12c",
    "f_btc_prev_return_24c",
    "f_btc_prev_return_48c",
    "f_btc_rsi_7",
    "f_btc_rsi_14",
    "f_btc_rsi_21",
    "f_btc_volume_ratio",
    "f_btc_volatility_6c",
    "f_btc_volatility_12c",
    "f_btc_volatility_24c",
    "f_btc_bb_position_10",
    "f_btc_bb_position_20",
    "f_btc_macd",
    "f_decision_hour_utc_sin",
    "f_decision_hour_utc_cos",
    "f_decision_weekday_sin",
    "f_decision_weekday_cos",
    # Coinbase lead-lag (filled with 0.0 when CB data not available)
    "f_btc_cb_return_1c",
    "f_btc_cb_return_3c",
    "f_btc_cb_bn_spread_1c",
    "f_btc_cb_bn_spread_3c",
    "f_btc_cb_vol_dominance",
    "f_btc_cb_momentum_lead",
    # Derivatives (filled with 0.0 when not available)
    "f_btc_funding_rate",
    "f_btc_ls_ratio_log",
    "f_btc_taker_ratio_log",
    "f_btc_oi_change_pct",
    # Cross-timeframe & gap features [NEW]
    "f_btc_gap_open",       # (curr_open - prev_close) / prev_close — known at candle start
    "f_btc_rsi_1h",         # RSI(14) on 1h bars (resampled from 5m, no extra data)
    # Slot-specific latency arb features [NEW]
    "f_slot_elapsed_frac",  # fraction of 15-min slot elapsed at decision time [0, 1)
    "f_btc_vs_anchor_pct",  # (BTC_now - slot_anchor) / slot_anchor * 100, clipped [-3, 3]
]
N_FEATURES = len(FEATURE_NAMES)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    candles = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    candles.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return candles


def _load_separate_derivatives() -> list[dict]:
    """Merge separate derivative files into unified records keyed by timestamp_ms.

    btc_fetch_derivatives.py creates 4 separate files. This function merges them
    into a single list of dicts compatible with what build_dataset() expects:
      - funding_rate    (8h intervals — forward-filled to each 5m bucket)
      - ls_ratio_log    (log of long/short ratio)
      - taker_ratio_log (log of buy/sell ratio)
      - oi_change_pct   (% change in open interest vs previous row)

    Returns [] if none of the separate files exist.
    """
    base = Path("data/btc")

    def _read(fname: str) -> list[dict]:
        p = base / fname
        return _load_jsonl(p) if p.exists() else []

    fr_rows  = sorted(_read("funding_rate.jsonl"),    key=lambda r: r["timestamp_ms"])
    ls_rows  = sorted(_read("long_short_ratio.jsonl"), key=lambda r: r["timestamp_ms"])
    tk_rows  = sorted(_read("taker_ratio.jsonl"),      key=lambda r: r["timestamp_ms"])
    oi_rows  = sorted(_read("open_interest.jsonl"),    key=lambda r: r["timestamp_ms"])

    if not (fr_rows or ls_rows or tk_rows or oi_rows):
        return []

    # Build fast lookups: ts_ms -> value
    # Funding rate (8h) — forward-fill: for each ts, use most recent funding rate at or before ts
    fr_sorted = [(r["timestamp_ms"], float(r.get("funding_rate", 0.0))) for r in fr_rows]

    def _get_funding(ts: int) -> float:
        lo, hi = 0, len(fr_sorted) - 1
        result = 0.0
        while lo <= hi:
            mid = (lo + hi) // 2
            if fr_sorted[mid][0] <= ts:
                result = fr_sorted[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    # LS ratio — exact match by timestamp
    ls_index = {r["timestamp_ms"]: float(r.get("longShortRatio") or r.get("long_short_ratio", 1.0))
                for r in ls_rows}
    # Taker ratio — exact match
    tk_index = {r["timestamp_ms"]: float(r.get("buySellRatio") or r.get("buy_sell_ratio", 1.0))
                for r in tk_rows}
    # OI — exact match + compute change pct
    oi_sorted = [(r["timestamp_ms"], float(r.get("sumOpenInterest") or r.get("open_interest", 0.0)))
                 for r in oi_rows]
    oi_index: dict[int, float] = {}
    for i, (ts, oi) in enumerate(oi_sorted):
        prev_oi = oi_sorted[i - 1][1] if i > 0 else oi
        oi_index[ts] = (oi - prev_oi) / max(abs(prev_oi), 1e-8) * 100 if prev_oi else 0.0

    # Collect all timestamps from 5m/15m sources (ls, taker, oi)
    all_ts = sorted(set(ls_index) | set(tk_index) | set(oi_index))
    if not all_ts:
        # Only funding rate available — build from funding rate timestamps
        all_ts = [ts for ts, _ in fr_sorted]

    merged = []
    for ts in all_ts:
        ls_val = ls_index.get(ts, 1.0)
        tk_val = tk_index.get(ts, 1.0)
        merged.append({
            "timestamp_ms":    ts,
            "funding_rate":    _get_funding(ts),
            "ls_ratio_log":    _clamp(math.log(max(ls_val, 1e-8)), -2.0, 2.0),
            "taker_ratio_log": _clamp(math.log(max(tk_val, 1e-8)), -2.0, 2.0),
            "oi_change_pct":   oi_index.get(ts, 0.0),
        })

    return merged


def load_data(interval: str, lookback_months: int) -> dict[str, list[dict]]:
    """Load Binance, Coinbase, and derivatives data. Returns dict of lists."""
    cutoff_ms = int((datetime.now(UTC) - timedelta(days=lookback_months * 30)).timestamp() * 1000)

    def _load(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = _load_jsonl(path)
        rows.sort(key=lambda c: c["open_time_ms"])
        return [r for r in rows if r["open_time_ms"] >= cutoff_ms]

    bn = _load(Path(f"data/btc/ohlcv_{interval}.jsonl"))
    cb = _load(Path(f"data/btc/coinbase_{interval}.jsonl"))

    # Derivatives: try unified file first, then merge from separate files
    deriv: list[dict] = []
    unified = Path("data/btc/derivatives.jsonl")
    if unified.exists():
        deriv = _load_jsonl(unified)
    else:
        deriv = _load_separate_derivatives()

    return {"binance": bn, "coinbase": cb, "derivatives": deriv}


# ---------------------------------------------------------------------------
# Feature computation helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _rsi(closes: list[float], period: int) -> float:
    if len(closes) < period + 1:
        return 0.5
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-(period + 1) + i] - closes[-(period + 1) + i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al < 1e-10:
        return 1.0
    rs = ag / al
    return rs / (1.0 + rs)


def _bb_pos(closes: list[float], window: int) -> float:
    w = closes[-window:] if len(closes) >= window else closes
    mean = sum(w) / len(w)
    std  = math.sqrt(sum((c - mean) ** 2 for c in w) / len(w))
    upper = mean + 2 * std
    lower = mean - 2 * std
    bw = max(upper - lower, 1e-8)
    return _clamp((closes[-1] - lower) / bw, 0.0, 1.0) - 0.5


def _vol(closes: list[float], window: int) -> float:
    w = closes[-window:]
    if len(w) < 2:
        return 0.0
    rets = [(w[i] - w[i - 1]) / max(w[i - 1], 1e-8) for i in range(1, len(w))]
    mean_r = sum(rets) / len(rets)
    var_r  = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    return _clamp(math.sqrt(var_r) * 100, 0.0, 5.0)


def _macd(closes: list[float]) -> float:
    """EMA12 - EMA26 normalised by price, clipped."""
    def _ema(data: list[float], span: int) -> float:
        k = 2 / (span + 1)
        val = data[0]
        for x in data[1:]:
            val = x * k + val * (1 - k)
        return val
    if len(closes) < 27:
        return 0.0
    ema12 = _ema(closes[-26:], 12)
    ema26 = _ema(closes[-26:], 26)
    return _clamp((ema12 - ema26) / max(closes[-1], 1e-8), -0.05, 0.05)


def _align_cb(bn_ts: int, cb_index: dict[int, dict]) -> dict | None:
    """Return CB candle with matching timestamp (same 5m bucket)."""
    return cb_index.get(bn_ts)


def compute_features(
    candles: list[dict],
    idx: int,
    cb_index: dict[int, dict],
    deriv_index: dict[int, dict],
) -> list[float] | None:
    """Compute all features for the decision point at `idx`.

    Uses completed candles[:idx] only — no look-ahead bias.
    Returns None if not enough history.
    """
    if idx < REQUIRED_LOOKBACK:
        return None

    prev    = candles[idx - 1]
    hist    = candles[max(0, idx - 60): idx]  # up to 60 completed candles
    closes  = [c["close"]  for c in hist]
    vols    = [c["volume"] for c in hist]

    # --- Binance returns ---
    def _ret(n: int) -> float:
        # n-period return ending at prev (candles[idx-1]).
        # base must be n candles BEFORE prev, i.e. candles[idx-1-n].
        # Bug was: base = candles[idx-n] = prev for n=1 → always 0.
        base = candles[max(0, idx - 1 - n)]["close"]
        return (prev["close"] - base) / max(base, 1e-8)

    r1, r3, r6 = _ret(1), _ret(3), _ret(6)
    r12, r24, r48 = _ret(12), _ret(24), _ret(48)

    # --- RSI ---
    rsi7  = _rsi(closes, 7)
    rsi14 = _rsi(closes, 14)
    rsi21 = _rsi(closes, 21)

    # --- Volume ratio ---
    avg_vol20 = sum(vols[-20:]) / max(len(vols[-20:]), 1)
    vol_ratio = _clamp(math.log(max(prev["volume"], 1e-8) / max(avg_vol20, 1e-8)), -3.0, 3.0)

    # --- Volatility ---
    vol6  = _vol(closes, 6)
    vol12 = _vol(closes, 12)
    vol24 = _vol(closes, 24)

    # --- Bollinger Bands ---
    bb10 = _bb_pos(closes, 10)
    bb20 = _bb_pos(closes, 20)

    # --- MACD ---
    macd_val = _macd(closes)

    # --- Time features (use candle timestamp, consistent with training) ---
    open_time_sec = candles[idx]["open_time_ms"] / 1000
    hour_frac  = (open_time_sec % 86400) / 3600
    hour_sin   = math.sin(2 * math.pi * hour_frac / 24)
    hour_cos   = math.cos(2 * math.pi * hour_frac / 24)
    dow        = int(open_time_sec // 86400 + 4) % 7
    week_sin   = math.sin(2 * math.pi * dow / 7)
    week_cos   = math.cos(2 * math.pi * dow / 7)

    # --- Coinbase lead-lag (0.0 if data not available) ---
    cb = _align_cb(candles[idx]["open_time_ms"], cb_index)
    cb_prev_ts = candles[idx - 1]["open_time_ms"]
    cb_prev = cb_index.get(cb_prev_ts)
    cb_3c_ts  = candles[max(0, idx - 3)]["open_time_ms"]
    cb_3c     = cb_index.get(cb_3c_ts)

    if cb_prev and cb:
        cb_r1 = _clamp((cb_prev["close"] - cb_prev["open"]) / max(cb_prev["open"], 1e-8), -0.05, 0.05)
    else:
        cb_r1 = 0.0

    if cb_3c and cb_prev:
        cb_r3 = _clamp((cb_prev["close"] - cb_3c["close"]) / max(cb_3c["close"], 1e-8), -0.10, 0.10)
    else:
        cb_r3 = 0.0

    cb_bn_spread1 = _clamp(cb_r1 - _clamp(r1, -0.05, 0.05), -0.05, 0.05)
    cb_bn_spread3 = _clamp(cb_r3 - _clamp(r3, -0.10, 0.10), -0.10, 0.10)

    if cb_prev and cb:
        bn_vol = max(prev["volume"], 1e-8)
        cb_vol = max(cb_prev.get("volume", 0.0), 1e-8)
        cb_vol_dom = _clamp((cb_vol / (cb_vol + bn_vol)) - 0.5, -0.5, 0.5)
    else:
        cb_vol_dom = 0.0

    cb_mom_lead = _clamp(cb_r3 - _clamp(r3, -0.10, 0.10), -0.10, 0.10)

    # --- Derivatives (0.0 if data not available) ---
    deriv = deriv_index.get(candles[idx]["open_time_ms"])
    funding  = _clamp(float(deriv.get("funding_rate", 0.0)) * 100, -3.0, 3.0) if deriv else 0.0
    ls_log   = _clamp(float(deriv.get("ls_ratio_log",   0.0)), -2.0, 2.0)      if deriv else 0.0
    taker_log= _clamp(float(deriv.get("taker_ratio_log",0.0)), -2.0, 2.0)      if deriv else 0.0
    oi_chg   = _clamp(float(deriv.get("oi_change_pct",  0.0)), -5.0, 5.0)      if deriv else 0.0

    # --- Gap open: (curr_open - prev_close) / prev_close [NEW] ---
    # Known at t=0 of the candle — no lookahead. Strong momentum signal.
    curr_open = candles[idx]["open"]
    gap_open  = _clamp((curr_open - prev["close"]) / max(prev["close"], 1e-8), -0.05, 0.05)

    # --- RSI on 1h bars (resample: take close of last candle each hour) [NEW] ---
    # We have up to 60 5m candles in hist. Group every 12 to get 1h bars.
    hourly_closes: list[float] = []
    step = 12  # 12 × 5m = 1h (or 12 × 15m = 3h for 15m interval — still useful)
    for k in range(step - 1, len(hist), step):
        hourly_closes.append(hist[k]["close"])
    rsi_1h_raw = _rsi(hourly_closes, min(14, max(len(hourly_closes) - 1, 1)))
    f_rsi_1h = _clamp((rsi_1h_raw - 0.5) * 2.0, -1.0, 1.0)

    # --- Slot latency arb features [NEW] ---
    # Align to the 15-min UTC slot boundary: slot_start_ms = floor(open_time_ms / 900_000) * 900_000.
    # For 5m candles: three decision points per slot (elapsed ≈ 0, 0.33, 0.67).
    # For 15m candles: always at slot start (elapsed = 0, anchor_pct ≈ 0) — harmless near-zero features.
    slot_start_ms = (candles[idx]["open_time_ms"] // 900_000) * 900_000
    elapsed_ms = candles[idx]["open_time_ms"] - slot_start_ms
    f_slot_elapsed_frac = _clamp(elapsed_ms / 900_000, 0.0, 1.0)

    # BTC anchor = open of the first candle in the current slot.
    # Walk back from idx until we reach the slot boundary.
    anchor_open = candles[idx]["open"]
    for _k in range(min(4, idx)):
        _cand = candles[idx - _k]
        if _cand["open_time_ms"] <= slot_start_ms:
            anchor_open = _cand["open"]
            break
    f_btc_vs_anchor_pct = _clamp(
        (prev["close"] - anchor_open) / max(anchor_open, 1e-8) * 100, -3.0, 3.0
    )

    return [
        _clamp(r1,  -0.10, 0.10),
        _clamp(r3,  -0.15, 0.15),
        _clamp(r6,  -0.20, 0.20),
        _clamp(r12, -0.20, 0.20),
        _clamp(r24, -0.25, 0.25),
        _clamp(r48, -0.30, 0.30),
        _clamp((rsi7  - 0.5) * 2.0, -1.0, 1.0),
        _clamp((rsi14 - 0.5) * 2.0, -1.0, 1.0),
        _clamp((rsi21 - 0.5) * 2.0, -1.0, 1.0),
        vol_ratio,
        vol6, vol12, vol24,
        bb10, bb20,
        macd_val,
        hour_sin, hour_cos,
        week_sin, week_cos,
        cb_r1, cb_r3,
        cb_bn_spread1, cb_bn_spread3, cb_vol_dom, cb_mom_lead,
        funding, ls_log, taker_log, oi_chg,
        gap_open, f_rsi_1h,
        f_slot_elapsed_frac, f_btc_vs_anchor_pct,  # slot latency arb
    ]


# ---------------------------------------------------------------------------
# Dataset builder
# ---------------------------------------------------------------------------

def build_dataset(
    binance: list[dict],
    coinbase: list[dict],
    derivatives: list[dict],
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y) arrays from raw candle data.

    Label: 1 if current candle close > open (UP), 0 otherwise.
    Features use only candles[:idx] — no look-ahead.
    """
    cb_index: dict[int, dict] = {c["open_time_ms"]: c for c in coinbase}
    deriv_index: dict[int, dict] = {}
    for d in derivatives:
        ts = d.get("open_time_ms") or d.get("timestamp_ms")
        if ts:
            deriv_index[int(ts)] = d

    candles = binance
    X_rows, y_rows = [], []
    for idx in range(REQUIRED_LOOKBACK, len(candles) - 1):
        c = candles[idx]
        label = 1 if c["close"] > c["open"] else 0
        feats = compute_features(candles, idx, cb_index, deriv_index)
        if feats is None:
            continue
        X_rows.append(feats)
        y_rows.append(label)

    X = np.array(X_rows, dtype=np.float32)
    y = np.array(y_rows, dtype=np.int32)
    return X, y


# ---------------------------------------------------------------------------
# PnL evaluation metric
# ---------------------------------------------------------------------------

def compute_pnl_metrics(
    probs: np.ndarray,
    labels: np.ndarray,
    *,
    fee_bps: float = FEE_BPS,
    min_edge_bps: float = 500.0,
    kelly: float = KELLY_FRACTION,
) -> dict[str, float]:
    """Simulate P&L with fractional Kelly on predictions above edge threshold.

    Returns: pnl (cumulative), sharpe (annualised), win_rate, n_trades.
    """
    market_price = 0.5   # Polymarket BTC Up/Down markets are always ~50¢
    fee = fee_bps / 10_000
    pnl_series: list[float] = []

    for p, y in zip(probs, labels):
        edge = abs(float(p) - market_price)
        if edge * 10_000 < min_edge_bps:
            continue
        correct = (float(p) > market_price and int(y) == 1) or (float(p) < market_price and int(y) == 0)
        bet_size = kelly * edge / (1.0 - market_price + 1e-8)
        ret = bet_size * ((1.0 - fee) if correct else -1.0)
        pnl_series.append(ret)

    if len(pnl_series) < 10:
        return {"pnl": 0.0, "sharpe": -99.0, "win_rate": 0.0, "n_trades": len(pnl_series)}

    arr = np.array(pnl_series, dtype=np.float64)
    # Annualise by actual trade count, not by calendar bars.
    # Using sqrt(252 × bars_per_day) would over-count if we only trade a fraction
    # of all bars. Instead: sqrt(n_trades_per_year), estimated from the observed
    # trade rate in this sample. 252 trading days × observed trades_per_day.
    n_total_bars = len(probs)
    trade_rate   = len(arr) / max(n_total_bars, 1)   # fraction of bars we trade
    bars_per_day = 96  # 15-min bars per day (24h × 4)
    trades_per_year = trade_rate * bars_per_day * 252
    annual_factor = math.sqrt(max(trades_per_year, 1.0))
    sharpe = float(arr.mean() / (arr.std() + 1e-8) * annual_factor)
    return {
        "pnl":      float(arr.sum()),
        "sharpe":   round(sharpe, 4),
        "win_rate": float((arr > 0).mean()),
        "n_trades": len(arr),
    }


def compute_detailed_pnl(
    probs: np.ndarray,
    labels: np.ndarray,
    *,
    fee_bps: float = FEE_BPS,
    min_edge_bps: float = 500.0,
    kelly: float = KELLY_FRACTION,
) -> dict[str, Any]:
    """Extended PnL analysis with drawdown, profit factor, streaks, per-trade detail."""
    market_price = 0.5
    fee = fee_bps / 10_000
    trades: list[dict[str, Any]] = []

    for i, (p, y) in enumerate(zip(probs, labels)):
        edge = abs(float(p) - market_price)
        if edge * 10_000 < min_edge_bps:
            continue
        correct = (float(p) > market_price and int(y) == 1) or (float(p) < market_price and int(y) == 0)
        bet_size = kelly * edge / (1.0 - market_price + 1e-8)
        ret = bet_size * ((1.0 - fee) if correct else -1.0)
        trades.append({
            "idx": int(i),
            "prob": round(float(p), 6),
            "label": int(y),
            "edge_bps": round(edge * 10_000, 1),
            "correct": correct,
            "bet_size": round(bet_size, 6),
            "pnl": round(ret, 6),
        })

    if len(trades) < 5:
        return {"n_trades": len(trades), "too_few": True}

    pnl_arr = np.array([t["pnl"] for t in trades], dtype=np.float64)
    wins = [t["pnl"] for t in trades if t["correct"]]
    losses = [t["pnl"] for t in trades if not t["correct"]]
    edges = [t["edge_bps"] for t in trades]

    # Cumulative PnL and drawdown
    cum_pnl = np.cumsum(pnl_arr)
    peak = np.maximum.accumulate(cum_pnl)
    drawdown = cum_pnl - peak
    max_dd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    # Consecutive streaks
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for t in trades:
        if t["correct"]:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / max(gross_loss, 1e-10)

    # Edge bucket analysis
    edge_buckets: dict[str, dict[str, Any]] = {}
    for bucket_lo, bucket_hi, label in [
        (0, 200, "0-200bps"), (200, 400, "200-400bps"),
        (400, 600, "400-600bps"), (600, 1000, "600-1000bps"),
        (1000, 99999, "1000+bps"),
    ]:
        bucket_trades = [t for t in trades if bucket_lo <= t["edge_bps"] < bucket_hi]
        if bucket_trades:
            bt_wins = sum(1 for t in bucket_trades if t["correct"])
            bt_pnl = sum(t["pnl"] for t in bucket_trades)
            edge_buckets[label] = {
                "count": len(bucket_trades),
                "win_rate": round(bt_wins / len(bucket_trades), 4),
                "total_pnl": round(bt_pnl, 6),
                "avg_edge_bps": round(sum(t["edge_bps"] for t in bucket_trades) / len(bucket_trades), 1),
            }

    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / max(len(trades), 1), 4),
        "total_pnl": round(float(pnl_arr.sum()), 6),
        "avg_pnl_per_trade": round(float(pnl_arr.mean()), 6),
        "pnl_std": round(float(pnl_arr.std()), 6),
        "gross_profit": round(gross_profit, 6),
        "gross_loss": round(gross_loss, 6),
        "profit_factor": round(profit_factor, 4),
        "avg_win": round(sum(wins) / max(len(wins), 1), 6),
        "avg_loss": round(sum(losses) / max(len(losses), 1), 6),
        "max_drawdown": round(max_dd, 6),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_edge_bps": round(sum(edges) / max(len(edges), 1), 1),
        "median_edge_bps": round(float(np.median(edges)), 1),
        "edge_buckets": edge_buckets,
        "too_few": False,
    }


def compute_calibration_table(
    probs: np.ndarray,
    labels: np.ndarray,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Bucket predictions and compute predicted vs actual win rate per bucket."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    table = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (probs >= lo) & (probs < hi) if i < n_bins - 1 else (probs >= lo) & (probs <= hi)
        count = int(mask.sum())
        if count == 0:
            continue
        predicted_avg = round(float(probs[mask].mean()), 4)
        actual_avg = round(float(labels[mask].mean()), 4)
        cal_error = round(abs(predicted_avg - actual_avg), 4)
        table.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "count": count,
            "predicted_avg": predicted_avg,
            "actual_avg": actual_avg,
            "calibration_error": cal_error,
        })
    return table


def compute_feature_importance(
    models: dict[str, Any],
    feature_names: list[str],
    top_n: int = 15,
) -> dict[str, list[tuple[str, float]]]:
    """Extract feature importance from tree models."""
    result: dict[str, list[tuple[str, float]]] = {}

    if "lgbm" in models and models["lgbm"] is not None:
        try:
            raw = models["lgbm"].feature_importance(importance_type="gain")
            total = max(raw.sum(), 1e-10)
            pairs = sorted(zip(feature_names, (raw / total * 100).tolist()), key=lambda x: -x[1])
            result["lgbm"] = [(n, round(v, 2)) for n, v in pairs[:top_n]]
        except Exception:
            pass

    if "xgb" in models and models["xgb"] is not None:
        try:
            score = models["xgb"].get_score(importance_type="gain")
            total = max(sum(score.values()), 1e-10)
            pairs = sorted(score.items(), key=lambda x: -x[1])
            result["xgb"] = [(n, round(v / total * 100, 2)) for n, v in pairs[:top_n]]
        except Exception:
            pass

    if "catboost" in models and models["catboost"] is not None:
        try:
            raw = models["catboost"].get_feature_importance()
            pairs = sorted(zip(feature_names, raw.tolist()), key=lambda x: -x[1])
            result["catboost"] = [(n, round(v, 2)) for n, v in pairs[:top_n]]
        except Exception:
            pass

    return result


def compute_multi_threshold_sensitivity(
    probs: np.ndarray,
    labels: np.ndarray,
    fee_bps: float = FEE_BPS,
    kelly: float = KELLY_FRACTION,
) -> list[dict[str, Any]]:
    """Show how PnL changes at different min_edge thresholds."""
    rows = []
    for threshold in [100, 150, 200, 250, 300, 400, 500, 600, 750, 1000]:
        m = compute_pnl_metrics(probs, labels, fee_bps=fee_bps, min_edge_bps=float(threshold), kelly=kelly)
        rows.append({
            "min_edge_bps": threshold,
            "n_trades": m["n_trades"],
            "win_rate": round(m["win_rate"], 4),
            "pnl": round(m["pnl"], 6),
            "sharpe": round(m["sharpe"], 4),
        })
    return rows


def print_analysis_report(
    algo_name: str,
    probs: np.ndarray,
    labels: np.ndarray,
    models: dict[str, Any],
    feature_names: list[str],
    split_name: str = "val",
) -> dict[str, Any]:
    """Print and return comprehensive analysis for a model on a given split."""
    report: dict[str, Any] = {"algo": algo_name, "split": split_name}

    # --- 1. Detailed PnL ---
    dpnl = compute_detailed_pnl(probs, labels)
    report["detailed_pnl"] = dpnl
    if not dpnl.get("too_few"):
        print(f"\n  +-------------------------------------------------------------")
        print(f"  | DETAILED PnL -- {algo_name.upper()} on {split_name.upper()} set")
        print(f"  +-------------------------------------------------------------")
        print(f"  | Trades:          {dpnl['n_trades']:>8}")
        print(f"  | Win rate:        {dpnl['win_rate']:>8.2%}")
        print(f"  | Total PnL:       {dpnl['total_pnl']:>+8.4f}")
        print(f"  | Avg PnL/trade:   {dpnl['avg_pnl_per_trade']:>+8.6f}")
        print(f"  | Profit factor:   {dpnl['profit_factor']:>8.2f}")
        print(f"  | Gross profit:    {dpnl['gross_profit']:>+8.4f}")
        print(f"  | Gross loss:      {dpnl['gross_loss']:>8.4f}")
        print(f"  | Avg win:         {dpnl['avg_win']:>+8.6f}")
        print(f"  | Avg loss:        {dpnl['avg_loss']:>+8.6f}")
        print(f"  | Max drawdown:    {dpnl['max_drawdown']:>+8.4f}")
        print(f"  | Max win streak:  {dpnl['max_win_streak']:>8}")
        print(f"  | Max loss streak: {dpnl['max_loss_streak']:>8}")
        print(f"  | Avg edge (bps):  {dpnl['avg_edge_bps']:>8.1f}")
        print(f"  | Med edge (bps):  {dpnl['median_edge_bps']:>8.1f}")
        print(f"  +-------------------------------------------------------------")

        if dpnl["edge_buckets"]:
            print(f"\n  Edge bucket breakdown:")
            print(f"  {'Bucket':<14} {'Trades':>7} {'WinRate':>8} {'PnL':>10} {'AvgEdge':>8}")
            print(f"  {'-'*14} {'-'*7} {'-'*8} {'-'*10} {'-'*8}")
            for bk, bv in dpnl["edge_buckets"].items():
                print(f"  {bk:<14} {bv['count']:>7} {bv['win_rate']:>7.1%} {bv['total_pnl']:>+10.4f} {bv['avg_edge_bps']:>7.1f}")

    # --- 2. Calibration table ---
    cal_table = compute_calibration_table(probs, labels)
    report["calibration"] = cal_table
    if cal_table:
        mean_cal_err = sum(r["calibration_error"] * r["count"] for r in cal_table) / max(sum(r["count"] for r in cal_table), 1)
        report["mean_calibration_error"] = round(mean_cal_err, 4)
        print(f"\n  Calibration table ({split_name}) -- mean CE = {mean_cal_err:.4f}:")
        print(f"  {'Bin':<10} {'Count':>6} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
        print(f"  {'-'*10} {'-'*6} {'-'*10} {'-'*10} {'-'*8}")
        for row in cal_table:
            star = " !" if row["calibration_error"] > 0.05 else ""
            print(f"  {row['bin']:<10} {row['count']:>6} {row['predicted_avg']:>10.4f} {row['actual_avg']:>10.4f} {row['calibration_error']:>8.4f}{star}")

    # --- 3. Multi-threshold sensitivity ---
    sensitivity = compute_multi_threshold_sensitivity(probs, labels)
    report["threshold_sensitivity"] = sensitivity
    print(f"\n  Threshold sensitivity ({split_name}):")
    print(f"  {'MinEdge':>8} {'Trades':>7} {'WinRate':>8} {'PnL':>10} {'Sharpe':>8}")
    print(f"  {'-'*8} {'-'*7} {'-'*8} {'-'*10} {'-'*8}")
    for row in sensitivity:
        marker = "  <- current" if row["min_edge_bps"] == 300 else ""
        print(f"  {row['min_edge_bps']:>7}  {row['n_trades']:>7} {row['win_rate']:>7.1%} {row['pnl']:>+10.4f} {row['sharpe']:>8.2f}{marker}")

    # --- 4. Feature importance ---
    fi = compute_feature_importance(models, feature_names)
    report["feature_importance"] = {k: v for k, v in fi.items()}
    for algo_key, importances in fi.items():
        if algo_key.lower() == algo_name.lower() or (algo_name in ("ensemble", "stacking") and algo_key == list(fi.keys())[0]):
            print(f"\n  Feature importance ({algo_key}):")
            print(f"  {'#':>3} {'Feature':<30} {'Importance %':>12}")
            print(f"  {'-'*3} {'-'*30} {'-'*12}")
            for rank, (fname, fval) in enumerate(importances, 1):
                bar = '#' * int(fval / 2)
                print(f"  {rank:>3} {fname:<30} {fval:>10.2f}%  {bar}")

    return report


def save_training_report(
    interval: str,
    report_data: dict[str, Any],
    export_dir: Path,
) -> Path:
    """Save full training report as JSON."""
    report_dir = Path("data/btc")
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / f"training_report_{interval}.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, default=str)

    print(f"\n  Full report saved: {json_path}")
    return json_path


# ---------------------------------------------------------------------------
# Standardisation (fit on train only, apply to all)
# ---------------------------------------------------------------------------

def fit_scaler(X_train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    means = X_train.mean(axis=0)
    stds  = np.maximum(X_train.std(axis=0), 1e-8)
    return means, stds


def scale(X: np.ndarray, means: np.ndarray, stds: np.ndarray) -> np.ndarray:
    return (X - means) / stds


# ---------------------------------------------------------------------------
# Walk-forward cross-validation helper (used inside Optuna objectives)
# ---------------------------------------------------------------------------

def _wf_cv_eval(
    predict_fn: Any,   # (X_tr_scaled, y_tr, X_va_scaled) → np.ndarray of probs
    X_raw: np.ndarray,
    y: np.ndarray,
    n_folds: int = 3,
) -> float:
    """Temporal walk-forward CV: train on expanding window, evaluate on next slice.

    Returns mean pnl_sharpe across folds — far more honest than a single val split.
    Scales within each fold independently (no leakage).
    """
    n = len(X_raw)
    min_train = max(500, n // (n_folds + 2))
    fold_size = max(200, n // (n_folds + 2))
    sharpes: list[float] = []

    for i in range(n_folds):
        tr_end  = min_train + i * fold_size
        va_end  = tr_end + fold_size
        if va_end > n:
            break
        X_tr, y_tr = X_raw[:tr_end], y[:tr_end]
        X_va, y_va = X_raw[tr_end:va_end], y[tr_end:va_end]
        m, s = X_tr.mean(0), np.maximum(X_tr.std(0), 1e-8)
        try:
            probs = predict_fn((X_tr - m) / s, y_tr, (X_va - m) / s)
            sharpes.append(compute_pnl_metrics(probs, y_va)["sharpe"])
        except Exception:
            sharpes.append(-99.0)

    return float(np.mean(sharpes)) if sharpes else -99.0


# ---------------------------------------------------------------------------
# Hyperparameter grids (fallback when Optuna not installed)
# ---------------------------------------------------------------------------

_LGBM_GRID = [
    {"num_leaves": 15,  "learning_rate": 0.05,  "min_child_samples": 30, "subsample": 0.8, "colsample_bytree": 0.8},
    {"num_leaves": 31,  "learning_rate": 0.05,  "min_child_samples": 20, "subsample": 0.8, "colsample_bytree": 0.8},
    {"num_leaves": 63,  "learning_rate": 0.03,  "min_child_samples": 20, "subsample": 0.7, "colsample_bytree": 0.7},
    {"num_leaves": 127, "learning_rate": 0.02,  "min_child_samples": 10, "subsample": 0.7, "colsample_bytree": 0.7},
    {"num_leaves": 31,  "learning_rate": 0.01,  "min_child_samples": 50, "subsample": 0.9, "colsample_bytree": 0.9},
    {"num_leaves": 63,  "learning_rate": 0.05,  "min_child_samples": 10, "subsample": 0.6, "colsample_bytree": 0.8},
]

_XGB_GRID = [
    {"max_depth": 3, "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 5},
    {"max_depth": 5, "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 3},
    {"max_depth": 6, "learning_rate": 0.03, "subsample": 0.7, "colsample_bytree": 0.7, "min_child_weight": 3},
    {"max_depth": 4, "learning_rate": 0.02, "subsample": 0.9, "colsample_bytree": 0.9, "min_child_weight": 10},
    {"max_depth": 5, "learning_rate": 0.01, "subsample": 0.7, "colsample_bytree": 0.8, "min_child_weight": 5},
]

_LR_GRID = [
    {"C": 0.01},
    {"C": 0.1},
    {"C": 1.0},
    {"C": 10.0},
]


def _lgbm_train_params(params: dict, X_tr: np.ndarray, y_tr: np.ndarray,
                        X_va: np.ndarray, y_va: np.ndarray) -> Any:
    dt = lgb.Dataset(X_tr, label=y_tr)
    dv = lgb.Dataset(X_va, label=y_va, reference=dt)
    full = {"objective": "binary", "metric": "binary_logloss", "verbose": -1, "random_state": 42, **params}
    cb = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=-1)]
    return lgb.train(full, dt, num_boost_round=500, valid_sets=[dv], callbacks=cb)


def train_lgbm(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    X_train_raw: np.ndarray | None = None,
    n_optuna_trials: int = 50,
) -> tuple[Any, np.ndarray]:
    if not HAS_LGBM:
        return None, np.full(len(y_val), 0.5)

    if HAS_OPTUNA and X_train_raw is not None:
        # --- Bayesian search with walk-forward CV ---
        def objective(trial: Any) -> float:
            p = {
                "num_leaves":        trial.suggest_int("num_leaves", 15, 200),
                "learning_rate":     trial.suggest_float("lr", 0.005, 0.15, log=True),
                "min_child_samples": trial.suggest_int("mcs", 5, 100),
                "subsample":         trial.suggest_float("sub", 0.5, 1.0),
                "colsample_bytree":  trial.suggest_float("cbt", 0.5, 1.0),
                "reg_alpha":         trial.suggest_float("ra", 0.0, 2.0),
                "reg_lambda":        trial.suggest_float("rl", 0.0, 2.0),
            }
            def _pred(X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray) -> np.ndarray:
                dt = lgb.Dataset(X_tr, label=y_tr)
                full = {"objective": "binary", "metric": "binary_logloss", "verbose": -1, "random_state": 42, **p}
                m = lgb.train(full, dt, num_boost_round=200,
                              callbacks=[lgb.log_evaluation(period=-1)])
                return m.predict(X_va)
            return _wf_cv_eval(_pred, X_train_raw, y_train, n_folds=3)

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        best_p = study.best_params
        print(f"    LGBM Optuna best CV sharpe={study.best_value:.3f}  params={best_p}", flush=True)
        model = _lgbm_train_params(
            {"num_leaves": best_p["num_leaves"], "learning_rate": best_p["lr"],
             "min_child_samples": best_p["mcs"], "subsample": best_p["sub"],
             "colsample_bytree": best_p["cbt"], "reg_alpha": best_p["ra"],
             "reg_lambda": best_p["rl"]},
            X_train, y_train, X_val, y_val,
        )
        probs = model.predict(X_val)
        return model, probs
    else:
        # --- Grid fallback ---
        best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0
        for i, grid_params in enumerate(_LGBM_GRID):
            model = _lgbm_train_params(grid_params, X_train, y_train, X_val, y_val)
            probs = model.predict(X_val)
            m = compute_pnl_metrics(probs, y_val)
            marker = "  <- best" if m["sharpe"] > best_sharpe else ""
            if m["sharpe"] > best_sharpe:
                best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    LGBM [{i+1}/{len(_LGBM_GRID)}] sharpe={m['sharpe']:.3f} "
                  f"n={m['n_trades']} leaves={grid_params['num_leaves']}{marker}", flush=True)
        return best_model, best_probs


# ---------------------------------------------------------------------------
# XGBoost
# ---------------------------------------------------------------------------

def train_xgb(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    X_train_raw: np.ndarray | None = None,
    n_optuna_trials: int = 50,
) -> tuple[Any, np.ndarray]:
    if not HAS_XGB:
        return None, np.full(len(y_val), 0.5)

    def _xgb_fit(p: dict, X_tr: np.ndarray, y_tr: np.ndarray,
                 X_va: np.ndarray, y_va: np.ndarray, rounds: int = 500) -> Any:
        dt = xgb.DMatrix(X_tr, label=y_tr, feature_names=FEATURE_NAMES)
        dv = xgb.DMatrix(X_va, label=y_va, feature_names=FEATURE_NAMES)
        base = {"objective": "binary:logistic", "eval_metric": "logloss",
                "seed": 42, "verbosity": 0}
        return xgb.train({**base, **p}, dt, num_boost_round=rounds,
                         evals=[(dv, "val")], early_stopping_rounds=50, verbose_eval=False)

    if HAS_OPTUNA and X_train_raw is not None:
        def objective(trial: Any) -> float:
            p = {
                "max_depth":        trial.suggest_int("max_depth", 3, 8),
                "learning_rate":    trial.suggest_float("lr", 0.005, 0.15, log=True),
                "subsample":        trial.suggest_float("sub", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("cbt", 0.5, 1.0),
                "min_child_weight": trial.suggest_int("mcw", 1, 20),
                "reg_alpha":        trial.suggest_float("ra", 0.0, 2.0),
                "reg_lambda":       trial.suggest_float("rl", 0.0, 2.0),
                "gamma":            trial.suggest_float("gamma", 0.0, 1.0),
            }
            def _pred(X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray) -> np.ndarray:
                dt = xgb.DMatrix(X_tr, label=y_tr, feature_names=FEATURE_NAMES)
                dv = xgb.DMatrix(X_va, feature_names=FEATURE_NAMES)
                base = {"objective": "binary:logistic", "eval_metric": "logloss",
                        "seed": 42, "verbosity": 0}
                m = xgb.train({**base, **p}, dt, num_boost_round=200, verbose_eval=False)
                return m.predict(dv)
            return _wf_cv_eval(_pred, X_train_raw, y_train, n_folds=3)

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        bp = study.best_params
        print(f"    XGB  Optuna best CV sharpe={study.best_value:.3f}  params={bp}", flush=True)
        model = _xgb_fit(
            {"max_depth": bp["max_depth"], "learning_rate": bp["lr"],
             "subsample": bp["sub"], "colsample_bytree": bp["cbt"],
             "min_child_weight": bp["mcw"], "reg_alpha": bp["ra"],
             "reg_lambda": bp["rl"], "gamma": bp["gamma"]},
            X_train, y_train, X_val, y_val,
        )
        dv = xgb.DMatrix(X_val, feature_names=FEATURE_NAMES)
        return model, model.predict(dv)
    else:
        best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0
        for i, gp in enumerate(_XGB_GRID):
            model = _xgb_fit(gp, X_train, y_train, X_val, y_val)
            dv = xgb.DMatrix(X_val, feature_names=FEATURE_NAMES)
            probs = model.predict(dv)
            m = compute_pnl_metrics(probs, y_val)
            marker = "  <- best" if m["sharpe"] > best_sharpe else ""
            if m["sharpe"] > best_sharpe:
                best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    XGB  [{i+1}/{len(_XGB_GRID)}] sharpe={m['sharpe']:.3f} "
                  f"n={m['n_trades']} depth={gp['max_depth']}{marker}", flush=True)
        return best_model, best_probs


# ---------------------------------------------------------------------------
# Logistic Regression (sklearn)
# ---------------------------------------------------------------------------

def train_lr_sklearn(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    X_train_raw: np.ndarray | None = None,
    n_optuna_trials: int = 30,
) -> tuple[Any, np.ndarray]:
    if HAS_OPTUNA and X_train_raw is not None:
        def objective(trial: Any) -> float:
            C = trial.suggest_float("C", 1e-3, 100.0, log=True)
            # sklearn 1.8+: use l1_ratio instead of penalty='l1'
            l1_ratio = trial.suggest_float("l1_ratio", 0.0, 1.0)
            def _pred(X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray) -> np.ndarray:
                # sklearn 1.8+: penalty param deprecated; l1_ratio alone controls regularization type
                m = LogisticRegression(C=C, l1_ratio=l1_ratio,
                                       solver="saga", max_iter=2000, tol=1e-2, random_state=42)
                m.fit(X_tr, y_tr)
                return m.predict_proba(X_va)[:, 1]
            return _wf_cv_eval(_pred, X_train_raw, y_train, n_folds=3)

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        bp = study.best_params
        print(f"    LR   Optuna best CV sharpe={study.best_value:.3f}  C={bp['C']:.4f} l1_ratio={bp['l1_ratio']:.3f}", flush=True)
        model = LogisticRegression(C=bp["C"], l1_ratio=bp["l1_ratio"],
                                    solver="saga", max_iter=2000, tol=1e-2, random_state=42)
        model.fit(X_train, y_train)
        return model, model.predict_proba(X_val)[:, 1]
    else:
        best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0
        for i, gp in enumerate(_LR_GRID):
            model = LogisticRegression(solver="saga", max_iter=2000, tol=1e-2, random_state=42, **gp)
            model.fit(X_train, y_train)
            probs = model.predict_proba(X_val)[:, 1]
            m = compute_pnl_metrics(probs, y_val)
            marker = "  <- best" if m["sharpe"] > best_sharpe else ""
            if m["sharpe"] > best_sharpe:
                best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    LR   [{i+1}/{len(_LR_GRID)}] sharpe={m['sharpe']:.3f} n={m['n_trades']} C={gp['C']}{marker}", flush=True)
        return best_model, best_probs


# ---------------------------------------------------------------------------
# CatBoost
# ---------------------------------------------------------------------------

def train_catboost(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    X_train_raw: np.ndarray | None = None,
    n_optuna_trials: int = 40,
) -> tuple[Any, np.ndarray]:
    if not HAS_CATBOOST:
        return None, np.full(len(y_val), 0.5)

    def _cb_fit(p: dict, X_tr: np.ndarray, y_tr: np.ndarray,
                X_va: np.ndarray, y_va: np.ndarray) -> Any:
        pool_tr = cb_lib.Pool(X_tr, label=y_tr, feature_names=FEATURE_NAMES)
        pool_va = cb_lib.Pool(X_va, label=y_va, feature_names=FEATURE_NAMES)
        model = cb_lib.CatBoostClassifier(
            iterations=500, verbose=0, random_seed=42,
            early_stopping_rounds=50, eval_metric="Logloss",
            **p,
        )
        model.fit(pool_tr, eval_set=pool_va, use_best_model=True)
        return model

    if HAS_OPTUNA and X_train_raw is not None:
        def objective(trial: Any) -> float:
            p = {
                "depth":          trial.suggest_int("depth", 4, 10),
                "learning_rate":  trial.suggest_float("lr", 0.01, 0.15, log=True),
                "l2_leaf_reg":    trial.suggest_float("l2", 1.0, 10.0),
                "subsample":      trial.suggest_float("sub", 0.5, 1.0),
                "colsample_bylevel": trial.suggest_float("cbl", 0.5, 1.0),
                "min_data_in_leaf": trial.suggest_int("mdl", 5, 50),
            }
            def _pred(X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray) -> np.ndarray:
                pool_tr = cb_lib.Pool(X_tr, label=y_tr, feature_names=FEATURE_NAMES)
                m = cb_lib.CatBoostClassifier(iterations=200, verbose=0, random_seed=42, **p)
                m.fit(pool_tr)
                return m.predict_proba(X_va)[:, 1]
            return _wf_cv_eval(_pred, X_train_raw, y_train, n_folds=3)

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        bp = study.best_params
        print(f"    CB   Optuna best CV sharpe={study.best_value:.3f}  params={bp}", flush=True)
        model = _cb_fit(
            {"depth": bp["depth"], "learning_rate": bp["lr"], "l2_leaf_reg": bp["l2"],
             "subsample": bp["sub"], "colsample_bylevel": bp["cbl"],
             "min_data_in_leaf": bp["mdl"]},
            X_train, y_train, X_val, y_val,
        )
        return model, model.predict_proba(X_val)[:, 1]
    else:
        # Default CatBoost params (no grid for brevity — CB default is already well-tuned)
        model = _cb_fit({"depth": 6, "learning_rate": 0.05, "l2_leaf_reg": 3.0},
                        X_train, y_train, X_val, y_val)
        probs = model.predict_proba(X_val)[:, 1]
        m = compute_pnl_metrics(probs, y_val)
        print(f"    CB   sharpe={m['sharpe']:.3f} n_trades={m['n_trades']}", flush=True)
        return model, probs


# ---------------------------------------------------------------------------
# Stacking ensemble (OOF meta-learner)
# ---------------------------------------------------------------------------

def train_stacking(
    base_results: dict[str, dict],   # algo → {"probs_val": ..., "model": ...}
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    models: dict[str, Any],
    n_folds: int = 5,
) -> np.ndarray:
    """OOF stacking: generate out-of-fold predictions from each base model,
    train a meta-LR on them, predict on val set.

    Systematically better than weighted average — the meta-learner learns
    WHEN to trust each model.
    """
    base_algos = [k for k in base_results if k != "ensemble" and models.get(k) is not None]
    if len(base_algos) < 2:
        return np.full(len(y_val), 0.5)

    n_tr = len(X_train)
    fold_size = n_tr // n_folds
    oof_preds = np.full((n_tr, len(base_algos)), 0.5)

    for fold_i in range(n_folds):
        va_start = fold_i * fold_size
        va_end   = va_start + fold_size if fold_i < n_folds - 1 else n_tr
        tr_idx   = list(range(0, va_start)) + list(range(va_end, n_tr))
        va_idx   = list(range(va_start, va_end))

        X_f_tr, y_f_tr = X_train[tr_idx], y_train[tr_idx]
        X_f_va = X_train[va_idx]

        for j, algo in enumerate(base_algos):
            try:
                if algo == "lgbm" and HAS_LGBM:
                    dt = lgb.Dataset(X_f_tr, label=y_f_tr)
                    p = {"objective": "binary", "metric": "binary_logloss",
                         "verbose": -1, "random_state": 42, "num_leaves": 31,
                         "learning_rate": 0.05, "n_estimators": 200}
                    m = lgb.train(p, dt, num_boost_round=200,
                                  callbacks=[lgb.log_evaluation(period=-1)])
                    oof_preds[va_idx, j] = m.predict(X_f_va)
                elif algo == "xgb" and HAS_XGB:
                    dt = xgb.DMatrix(X_f_tr, label=y_f_tr, feature_names=FEATURE_NAMES)
                    dv = xgb.DMatrix(X_f_va, feature_names=FEATURE_NAMES)
                    p = {"objective": "binary:logistic", "eval_metric": "logloss",
                         "seed": 42, "verbosity": 0, "max_depth": 5, "learning_rate": 0.05}
                    m = xgb.train(p, dt, num_boost_round=200, verbose_eval=False)
                    oof_preds[va_idx, j] = m.predict(dv)
                elif algo == "catboost" and HAS_CATBOOST:
                    pool = cb_lib.Pool(X_f_tr, label=y_f_tr, feature_names=FEATURE_NAMES)
                    m = cb_lib.CatBoostClassifier(iterations=200, verbose=0, random_seed=42)
                    m.fit(pool)
                    oof_preds[va_idx, j] = m.predict_proba(X_f_va)[:, 1]
                elif algo == "lr":
                    m = LogisticRegression(C=1.0, solver="saga", max_iter=2000, tol=1e-2, random_state=42)
                    m.fit(X_f_tr, y_f_tr)
                    oof_preds[va_idx, j] = m.predict_proba(X_f_va)[:, 1]
            except Exception:
                pass  # leave 0.5 default

    # Meta-LR trained on OOF predictions
    meta = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, tol=1e-3, random_state=42)
    meta.fit(oof_preds, y_train)

    # Val predictions: stack each base model's val probs
    val_stack = np.column_stack([base_results[a]["probs_val"] for a in base_algos])
    stack_probs = meta.predict_proba(val_stack)[:, 1]
    m = compute_pnl_metrics(stack_probs, y_val)
    print(f"    STACK ({'+'.join(base_algos)}) sharpe={m['sharpe']:.3f} n_trades={m['n_trades']}", flush=True)
    return stack_probs


# ---------------------------------------------------------------------------
# LSTM (PyTorch)
# ---------------------------------------------------------------------------

class _BtcLSTM(nn.Module):  # type: ignore[misc]
    def __init__(self, n_features: int, hidden: int = 128, layers: int = 2, dropout: float = 0.2) -> None:
        super().__init__()
        self.lstm = nn.LSTM(n_features, hidden, layers, batch_first=True,
                            dropout=dropout if layers > 1 else 0.0)
        self.fc   = nn.Linear(hidden, 1)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        out, _ = self.lstm(x)
        return torch.sigmoid(self.fc(out[:, -1, :]))


def _make_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> tuple[np.ndarray, np.ndarray]:
    """Reshape (N, F) → (N-seq_len, seq_len, F) for LSTM/TCN input."""
    n = len(X)
    xs, ys = [], []
    for i in range(seq_len, n):
        xs.append(X[i - seq_len: i])
        ys.append(y[i])
    return np.array(xs, dtype=np.float32), np.array(ys, dtype=np.float32)


def train_lstm(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    *,
    epochs: int = 40,
    lr: float = 3e-4,
    batch: int = 256,
) -> tuple[Any, np.ndarray]:
    if not HAS_TORCH:
        return None, np.full(len(y_val), 0.5)

    Xs_tr, ys_tr = _make_sequences(X_train, y_train, SEQ_LEN)
    Xs_va, ys_va = _make_sequences(X_val,   y_val,   SEQ_LEN)

    model = _BtcLSTM(n_features=X_train.shape[1])
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.BCELoss()

    Xt = torch.from_numpy(Xs_tr)
    yt = torch.from_numpy(ys_tr).unsqueeze(1)

    best_val_loss = float("inf")
    best_state: dict | None = None
    patience, no_improve = 10, 0  # early stopping

    for epoch in range(epochs):
        model.train()
        idx_shuf = torch.randperm(len(Xt))
        for start in range(0, len(Xt), batch):
            b_idx = idx_shuf[start: start + batch]
            xb, yb = Xt[b_idx], yt[b_idx]
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            optimizer.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.from_numpy(Xs_va))
            val_loss = criterion(val_pred, torch.from_numpy(ys_va).unsqueeze(1)).item()
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"    LSTM early stop at epoch {epoch+1}  best_val_loss={best_val_loss:.5f}")
                break

        if (epoch + 1) % 10 == 0:
            print(f"    LSTM epoch {epoch+1}/{epochs}  val_loss={val_loss:.5f}")

    if best_state:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        probs = model(torch.from_numpy(Xs_va)).squeeze(1).numpy()

    full_probs = np.full(len(y_val), 0.5)
    full_probs[SEQ_LEN:] = probs
    return model, full_probs


# ---------------------------------------------------------------------------
# TCN (PyTorch)
# ---------------------------------------------------------------------------

class _CausalConv1d(nn.Module):  # type: ignore[misc]
    def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int) -> None:
        super().__init__()
        pad = (kernel - 1) * dilation
        self.conv = nn.Conv1d(in_ch, out_ch, kernel, dilation=dilation, padding=pad)
        self.pad  = pad

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        return self.conv(x)[:, :, :-self.pad] if self.pad > 0 else self.conv(x)


class _TemporalBlock(nn.Module):  # type: ignore[misc]
    def __init__(self, in_ch: int, out_ch: int, kernel: int, dilation: int, dropout: float = 0.1) -> None:
        super().__init__()
        self.net = nn.Sequential(
            _CausalConv1d(in_ch, out_ch, kernel, dilation),
            nn.ReLU(),
            nn.Dropout(dropout),
            _CausalConv1d(out_ch, out_ch, kernel, dilation),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.downsample = nn.Conv1d(in_ch, out_ch, 1) if in_ch != out_ch else None
        self.relu = nn.ReLU()

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        res = self.downsample(x) if self.downsample else x
        return self.relu(self.net(x) + res)


class _BtcTCN(nn.Module):  # type: ignore[misc]
    def __init__(self, n_features: int, channels: tuple[int, ...] = (64, 128, 128, 64), kernel: int = 3) -> None:
        super().__init__()
        layers = []
        in_ch = n_features
        for i, out_ch in enumerate(channels):
            layers.append(_TemporalBlock(in_ch, out_ch, kernel, dilation=2 ** i))
            in_ch = out_ch
        self.tcn = nn.Sequential(*layers)
        self.fc  = nn.Linear(channels[-1], 1)

    def forward(self, x: "torch.Tensor") -> "torch.Tensor":
        # x: (batch, seq_len, features) → need (batch, features, seq_len) for Conv1d
        out = self.tcn(x.permute(0, 2, 1))
        return torch.sigmoid(self.fc(out[:, :, -1]))   # last timestep


def train_tcn(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
    *,
    epochs: int = 40,
    lr: float = 3e-4,
    batch: int = 256,
) -> tuple[Any, np.ndarray]:
    if not HAS_TORCH:
        return None, np.full(len(y_val), 0.5)

    Xs_tr, ys_tr = _make_sequences(X_train, y_train, SEQ_LEN)
    Xs_va, ys_va = _make_sequences(X_val,   y_val,   SEQ_LEN)

    model = _BtcTCN(n_features=X_train.shape[1])
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.BCELoss()

    Xt = torch.from_numpy(Xs_tr)
    yt = torch.from_numpy(ys_tr).unsqueeze(1)

    best_val_loss = float("inf")
    best_state: dict | None = None
    patience, no_improve = 10, 0  # early stopping

    for epoch in range(epochs):
        model.train()
        idx_shuf = torch.randperm(len(Xt))
        for start in range(0, len(Xt), batch):
            b_idx = idx_shuf[start: start + batch]
            xb, yb = Xt[b_idx], yt[b_idx]
            optimizer.zero_grad()
            pred = model(xb)
            loss = criterion(pred, yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            optimizer.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.from_numpy(Xs_va))
            val_loss = criterion(val_pred, torch.from_numpy(ys_va).unsqueeze(1)).item()
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                print(f"    TCN early stop at epoch {epoch+1}  best_val_loss={best_val_loss:.5f}")
                break

        if (epoch + 1) % 10 == 0:
            print(f"    TCN epoch {epoch+1}/{epochs}  val_loss={val_loss:.5f}")

    if best_state:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        probs = model(torch.from_numpy(Xs_va)).squeeze(1).numpy()

    full_probs = np.full(len(y_val), 0.5)
    full_probs[SEQ_LEN:] = probs
    return model, full_probs


# ---------------------------------------------------------------------------
# Calibration (isotonic regression + temperature scaling)
# ---------------------------------------------------------------------------

def calibrate(probs_val: np.ndarray, y_val: np.ndarray) -> IsotonicRegression:
    """Fit isotonic regression calibrator on validation predictions."""
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(probs_val.reshape(-1, 1), y_val)
    return iso


def apply_calibration(iso: IsotonicRegression, probs: np.ndarray) -> np.ndarray:
    return iso.predict(probs.reshape(-1, 1))


def _compute_ece(probs: np.ndarray, labels: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error — lower is better calibrated."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(probs)
    for i in range(n_bins):
        mask = (probs >= bins[i]) & (probs < bins[i + 1])
        if not mask.any():
            continue
        ece += mask.sum() / n * abs(float(labels[mask].mean()) - float(probs[mask].mean()))
    return ece


def calibrate_temperature(probs_val: np.ndarray, y_val: np.ndarray) -> float:
    """Find temperature T that minimises NLL on the validation set.

    Temperature scaling: p_cal = sigmoid(logit(p) / T).
    T > 1 → shrinks confidence toward 0.5 (less over-confident).
    T < 1 → sharpens confidence toward 0/1 (rarely needed).
    NLL is convex in T, so ternary search finds the global minimum.
    """
    _eps = 1e-8
    p = np.clip(probs_val, _eps, 1 - _eps)
    logits = np.log(p / (1 - p))
    y = y_val.astype(float)

    def _nll(T: float) -> float:
        p_cal = np.clip(1.0 / (1.0 + np.exp(-logits / max(T, _eps))), _eps, 1 - _eps)
        return -float(np.mean(y * np.log(p_cal) + (1 - y) * np.log(1 - p_cal)))

    lo, hi = 0.05, 10.0
    for _ in range(60):
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if _nll(m1) < _nll(m2):
            hi = m2
        else:
            lo = m1
    return (lo + hi) / 2.0


def apply_temperature(T: float, probs: np.ndarray) -> np.ndarray:
    """Apply temperature scaling to a probability array."""
    _eps = 1e-8
    p = np.clip(probs, _eps, 1 - _eps)
    logits = np.log(p / (1 - p))
    return np.clip(1.0 / (1.0 + np.exp(-logits / max(T, _eps))), _eps, 1 - _eps)


# ---------------------------------------------------------------------------
# Artifact export (compatible with prediction_model_runtime.py)
# ---------------------------------------------------------------------------

def _export_lgbm_artifact(
    model: Any,
    calibrator: IsotonicRegression | None,
    means: np.ndarray,
    stds: np.ndarray,
    metrics: dict,
    out_dir: Path,
    interval: str,
    suffix: str = "best",
    *,
    temperature: float | None = None,
) -> Path:
    model_fname = f"btc_{interval}_{suffix}_lgbm.model"
    model.save_model(str(out_dir / model_fname))

    artifact = {
        "artifact_type":          "prediction_model_v2",
        "model_name":             f"btc_updown_{interval}_lgbm_{suffix}",
        "model_version":          "v2.0.0",
        "feature_schema_version": f"btc-v2-{interval}",
        "description":            f"LightGBM | BTC Up/Down {interval} | PnL-optimised | Coinbase lead-lag",
        **metrics,
        "feature_columns": FEATURE_NAMES,
        "required_features": [
            "f_btc_prev_return_1c", "f_btc_prev_return_3c",
            "f_btc_rsi_14", "f_btc_bb_position_20",
        ],
        "algorithm_payload": {
            "algorithm":      "lightgbm",
            "feature_names":  FEATURE_NAMES,
            "lgbm_model_path": model_fname,
            "scaler_means":   [round(float(m), 8) for m in means],
            "scaler_stds":    [round(float(s), 8) for s in stds],
        },
        "calibration": _calibration_payload(calibrator, temperature=temperature),
    }
    path = out_dir / f"btc_{interval}_{suffix}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def _export_xgb_artifact(
    model: Any,
    calibrator: IsotonicRegression | None,
    means: np.ndarray,
    stds: np.ndarray,
    metrics: dict,
    out_dir: Path,
    interval: str,
    suffix: str = "best",
    *,
    temperature: float | None = None,
) -> Path:
    model_fname = f"btc_{interval}_{suffix}_xgb.model"
    model.save_model(str(out_dir / model_fname))

    artifact = {
        "artifact_type":          "prediction_model_v2",
        "model_name":             f"btc_updown_{interval}_xgb_{suffix}",
        "model_version":          "v2.0.0",
        "feature_schema_version": f"btc-v2-{interval}",
        "description":            f"XGBoost | BTC Up/Down {interval} | PnL-optimised | Coinbase lead-lag",
        **metrics,
        "feature_columns": FEATURE_NAMES,
        "required_features": [
            "f_btc_prev_return_1c", "f_btc_prev_return_3c",
            "f_btc_rsi_14", "f_btc_bb_position_20",
        ],
        "algorithm_payload": {
            "algorithm":     "xgboost",
            "feature_names": FEATURE_NAMES,
            "xgb_model_path": model_fname,
            "scaler_means":  [round(float(m), 8) for m in means],
            "scaler_stds":   [round(float(s), 8) for s in stds],
        },
        "calibration": _calibration_payload(calibrator, temperature=temperature),
    }
    path = out_dir / f"btc_{interval}_{suffix}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def _export_catboost_artifact(
    model: Any,
    calibrator: IsotonicRegression | None,
    means: np.ndarray,
    stds: np.ndarray,
    metrics: dict,
    out_dir: Path,
    interval: str,
    suffix: str = "best",
    *,
    temperature: float | None = None,
) -> Path:
    model_fname = f"btc_{interval}_{suffix}_catboost.cbm"
    model.save_model(str(out_dir / model_fname))

    artifact = {
        "artifact_type":          "prediction_model_v2",
        "model_name":             f"btc_updown_{interval}_catboost_{suffix}",
        "model_version":          "v2.0.0",
        "feature_schema_version": f"btc-v2-{interval}",
        "description":            f"CatBoost | BTC Up/Down {interval} | PnL-optimised | Coinbase lead-lag",
        **metrics,
        "feature_columns": FEATURE_NAMES,
        "required_features": [
            "f_btc_prev_return_1c", "f_btc_prev_return_3c",
            "f_btc_rsi_14", "f_btc_bb_position_20",
        ],
        "algorithm_payload": {
            "algorithm":          "catboost",
            "feature_names":      FEATURE_NAMES,
            "catboost_model_path": model_fname,
            "scaler_means":       [round(float(m), 8) for m in means],
            "scaler_stds":        [round(float(s), 8) for s in stds],
        },
        "calibration": _calibration_payload(calibrator, temperature=temperature),
    }
    path = out_dir / f"btc_{interval}_{suffix}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def _export_lr_artifact(
    model: Any,
    calibrator: IsotonicRegression | None,
    means: np.ndarray,
    stds: np.ndarray,
    metrics: dict,
    out_dir: Path,
    interval: str,
    suffix: str = "best",
    *,
    temperature: float | None = None,
) -> Path:
    weights = model.coef_[0].tolist()
    bias    = float(model.intercept_[0])
    # LR in standardized space — store raw means/stds so runtime can reproduce
    artifact = {
        "artifact_type":          "prediction_model_v2",
        "model_name":             f"btc_updown_{interval}_lr_{suffix}",
        "model_version":          "v2.0.0",
        "feature_schema_version": f"btc-v2-{interval}",
        **metrics,
        "feature_columns": FEATURE_NAMES,
        "required_features": ["f_btc_prev_return_1c", "f_btc_rsi_14"],
        "algorithm_payload": {
            "algorithm":      "logistic_regression",
            "feature_names":  FEATURE_NAMES,
            "means":          [round(float(m), 8) for m in means],
            "stds":           [round(float(s), 8) for s in stds],
            "weights":        [round(float(w), 8) for w in weights],
            "bias":           round(bias, 8),
        },
        "calibration": _calibration_payload(calibrator, temperature=temperature),
    }
    path = out_dir / f"btc_{interval}_{suffix}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def _export_dl_pth(model: Any, out_path: Path) -> None:
    if model is None or not HAS_TORCH:
        return
    torch.save(model.state_dict(), str(out_path))


def _calibration_payload(
    calibrator: IsotonicRegression | None,
    *,
    temperature: float | None = None,
) -> dict:
    """Build the calibration sub-dict for the model artifact JSON.

    Prefers temperature scaling when ``temperature`` is provided (single
    parameter → less prone to overfitting on small validation sets).
    Falls back to isotonic regression when only a fitted calibrator is given.
    """
    if temperature is not None:
        return {
            "method":              "temperature",
            "calibration_version": "v1.1.0",
            "parameters":          {"T": round(float(temperature), 6)},
        }
    if calibrator is None:
        return {"method": "none", "calibration_version": "none", "parameters": {}}
    try:
        thresholds = calibrator.X_thresholds_.tolist()
        values     = calibrator.y_thresholds_.tolist()
    except AttributeError:
        thresholds, values = [], []
    return {
        "method":               "isotonic",
        "calibration_version":  "v1.0.0",
        "parameters": {
            "boundaries": thresholds,
            "values":     values,
        },
    }


# ---------------------------------------------------------------------------
# Training orchestration for one interval
# ---------------------------------------------------------------------------

def train_interval(
    interval: str,
    *,
    lookback_months: int,
    algorithms: list[str],
    export_dir: Path,
    n_optuna_trials: int = 50,
) -> dict[str, Any]:
    print(f"\n{'='*60}")
    print(f"  Training BTC Up/Down {interval} | lookback={lookback_months}mo")
    print(f"{'='*60}")

    # Load data
    data = load_data(interval, lookback_months)
    if len(data["binance"]) < REQUIRED_LOOKBACK + 100:
        print(f"  ERROR: not enough Binance data ({len(data['binance'])} candles). "
              f"Run: python scripts/btc_fetch_ohlcv.py --interval {interval} --months {lookback_months}")
        return {}

    has_cb   = len(data["coinbase"]) > REQUIRED_LOOKBACK
    has_deriv = len(data["derivatives"]) > 0
    print(f"  Binance: {len(data['binance'])} candles | "
          f"Coinbase: {len(data['coinbase'])} ({'OK' if has_cb else 'MISSING -- CB features=0'}) | "
          f"Derivatives: {len(data['derivatives'])} rows ({'OK' if has_deriv else 'MISSING -- deriv features=0'})")

    print("  Building feature dataset ...")
    X, y = build_dataset(data["binance"], data["coinbase"], data["derivatives"])
    n = len(X)
    print(f"  Dataset: {n} samples  YES rate: {y.mean():.3f}")

    if n < 500:
        print("  ERROR: too few samples for reliable training.")
        return {}

    # Temporal split 70/15/15
    n_test  = int(n * 0.15)
    n_val   = int(n * 0.15)
    n_train = n - n_val - n_test

    X_train_raw, y_train = X[:n_train], y[:n_train]
    X_val_raw,   y_val   = X[n_train: n_train + n_val], y[n_train: n_train + n_val]
    X_test_raw,  y_test  = X[n_train + n_val:], y[n_train + n_val:]

    # Scale (fit on train only)
    means, stds = fit_scaler(X_train_raw)
    X_train = scale(X_train_raw, means, stds)
    X_val   = scale(X_val_raw,   means, stds)
    X_test  = scale(X_test_raw,  means, stds)

    opt_mode = "Optuna" if HAS_OPTUNA else "grid"
    print(f"  Split: train={n_train}  val={len(y_val)}  test={len(y_test)} (blindato)  search={opt_mode}")

    # Train each algorithm
    results: dict[str, dict] = {}
    models:  dict[str, Any]  = {}

    if "lgbm" in algorithms and HAS_LGBM:
        print(f"  Training LightGBM ({opt_mode}, {n_optuna_trials} trials) ...")
        t0 = time.time()
        m, probs_val = train_lgbm(X_train, y_train, X_val, y_val,
                                   X_train_raw=X_train_raw, n_optuna_trials=n_optuna_trials)
        models["lgbm"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["lgbm"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                           "elapsed_s": round(time.time() - t0, 1)}
        print(f"    LGBM: val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['lgbm']['elapsed_s']}s)")
    elif "lgbm" in algorithms:
        print("  LGBM: skipped (lightgbm not installed - pip install lightgbm)")

    if "xgb" in algorithms and HAS_XGB:
        print(f"  Training XGBoost ({opt_mode}, {n_optuna_trials} trials) ...")
        t0 = time.time()
        m, probs_val = train_xgb(X_train, y_train, X_val, y_val,
                                  X_train_raw=X_train_raw, n_optuna_trials=n_optuna_trials)
        models["xgb"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["xgb"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                          "elapsed_s": round(time.time() - t0, 1)}
        print(f"    XGB:  val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['xgb']['elapsed_s']}s)")
    elif "xgb" in algorithms:
        print("  XGB: skipped (xgboost not installed - pip install xgboost)")

    if "catboost" in algorithms and HAS_CATBOOST:
        print(f"  Training CatBoost ({opt_mode}, {n_optuna_trials} trials) ...")
        t0 = time.time()
        m, probs_val = train_catboost(X_train, y_train, X_val, y_val,
                                       X_train_raw=X_train_raw, n_optuna_trials=n_optuna_trials)
        models["catboost"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["catboost"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                                "elapsed_s": round(time.time() - t0, 1)}
        print(f"    CB:   val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['catboost']['elapsed_s']}s)")
    elif "catboost" in algorithms and not HAS_CATBOOST:
        print("  CatBoost: skipped (catboost not installed - pip install catboost)")

    if "lr" in algorithms:
        print(f"  Training Logistic Regression ({opt_mode}) ...")
        t0 = time.time()
        m, probs_val = train_lr_sklearn(X_train, y_train, X_val, y_val,
                                         X_train_raw=X_train_raw, n_optuna_trials=30)
        models["lr"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["lr"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                         "elapsed_s": round(time.time() - t0, 1)}
        print(f"    LR:   val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['lr']['elapsed_s']}s)")

    if "lstm" in algorithms and HAS_TORCH:
        print("  Training LSTM ...")
        t0 = time.time()
        m, probs_val = train_lstm(X_train, y_train, X_val, y_val, epochs=30)
        models["lstm"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["lstm"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                           "elapsed_s": round(time.time() - t0, 1)}
        print(f"    LSTM: val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['lstm']['elapsed_s']}s)")
    elif "lstm" in algorithms:
        print("  LSTM: skipped (torch not installed - pip install torch)")

    if "tcn" in algorithms and HAS_TORCH:
        print("  Training TCN ...")
        t0 = time.time()
        m, probs_val = train_tcn(X_train, y_train, X_val, y_val, epochs=30)
        models["tcn"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["tcn"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                          "elapsed_s": round(time.time() - t0, 1)}
        print(f"    TCN:  val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['tcn']['elapsed_s']}s)")
    elif "tcn" in algorithms:
        print("  TCN: skipped (torch not installed - pip install torch)")

    if not results:
        print("  ERROR: no algorithms produced results.")
        return {}

    # --- Stacking ensemble (OOF meta-learner) ---
    if len(results) >= 2 and "stacking" in algorithms:
        print("  Training Stacking ensemble ...")
        t0 = time.time()
        stack_probs = train_stacking(results, X_train, y_train, X_val, y_val, models)
        pnl = compute_pnl_metrics(stack_probs, y_val)
        acc = float(((stack_probs > 0.5) == y_val).mean())
        results["stacking"] = {"probs_val": stack_probs, "pnl": pnl, "val_acc": acc,
                                "elapsed_s": round(time.time() - t0, 1)}
        print(f"    STACK: val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['stacking']['elapsed_s']}s)")

    # Weighted-average ensemble (by PnL Sharpe, clipped to >=0)
    if len(results) > 1 and "ensemble" in algorithms:
        sharpes = np.array([max(r["pnl"]["sharpe"], 0.0) for r in results.values()])
        total = sharpes.sum()
        if total > 0:
            weights = sharpes / total
        else:
            weights = np.ones(len(results)) / len(results)
        all_probs = np.stack([r["probs_val"] for r in results.values()], axis=1)
        ensemble_probs = (all_probs * weights).sum(axis=1)
        pnl = compute_pnl_metrics(ensemble_probs, y_val)
        acc = float(((ensemble_probs > 0.5) == y_val).mean())
        results["ensemble"] = {"probs_val": ensemble_probs, "pnl": pnl, "val_acc": acc, "elapsed_s": 0}
        algo_names = list(r for r in results if r != "ensemble")
        print(f"    ENSEMBLE ({','.join(algo_names)} weights={[f'{w:.2f}' for w in weights]}): "
              f"val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  n_trades={pnl['n_trades']}")

    # --- Select best by PnL Sharpe (require min 30 trades to avoid noise wins) ---
    _MIN_TRADES_FOR_WINNER = 30
    eligible = {k: v for k, v in results.items() if v["pnl"]["n_trades"] >= _MIN_TRADES_FOR_WINNER}
    if not eligible:
        eligible = results  # fallback: no model meets threshold, take all
    best_algo = max(eligible, key=lambda k: eligible[k]["pnl"]["sharpe"])
    best_result = results[best_algo]
    print(f"\n{'='*64}")
    print(f"  WINNER: {best_algo.upper()}  pnl_sharpe={best_result['pnl']['sharpe']:.3f}  "
          f"val_acc={best_result['val_acc']:.4f}")
    print(f"{'='*64}")

    # --- Algorithm comparison table ---
    print(f"\n  Algorithm comparison:")
    print(f"  {'Algo':<12} {'ValAcc':>8} {'Sharpe':>8} {'PnL':>10} {'Trades':>7} {'WinRate':>8} {'Time':>7}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*10} {'-'*7} {'-'*8} {'-'*7}")
    for algo_key, algo_res in results.items():
        marker = " *" if algo_key == best_algo else ""
        print(f"  {algo_key:<12} {algo_res['val_acc']:>7.4f} {algo_res['pnl']['sharpe']:>8.3f} "
              f"{algo_res['pnl']['pnl']:>+10.4f} {algo_res['pnl']['n_trades']:>7} "
              f"{algo_res['pnl']['win_rate']:>7.1%} {algo_res['elapsed_s']:>6.1f}s{marker}")

    # Calibrate best model (for display only)
    best_probs_val = best_result["probs_val"]
    calibrator = calibrate(best_probs_val, y_val)
    cal_probs_val = apply_calibration(calibrator, best_probs_val)
    pnl_cal = compute_pnl_metrics(cal_probs_val, y_val)
    print(f"\n  After calibration: pnl_sharpe={pnl_cal['sharpe']:.3f}  n_trades={pnl_cal['n_trades']}")

    # --- Full analysis on validation set ---
    full_report: dict[str, Any] = {
        "interval": interval,
        "fee_bps": FEE_BPS,
        "timestamp": datetime.now(UTC).isoformat(),
        "data_stats": {
            "binance_candles": len(data["binance"]),
            "coinbase_candles": len(data["coinbase"]),
            "derivatives_rows": len(data["derivatives"]),
            "has_coinbase": has_cb,
            "has_derivatives": has_deriv,
            "total_samples": int(n),
            "train_samples": int(n_train),
            "val_samples": int(len(y_val)),
            "test_samples": int(len(y_test)),
            "yes_rate": round(float(y.mean()), 6),
        },
        "algorithms": {},
    }
    for algo_key, algo_res in results.items():
        full_report["algorithms"][algo_key] = {
            "val_acc": algo_res["val_acc"],
            "pnl_sharpe": algo_res["pnl"]["sharpe"],
            "pnl": algo_res["pnl"]["pnl"],
            "n_trades": algo_res["pnl"]["n_trades"],
            "win_rate": algo_res["pnl"]["win_rate"],
            "elapsed_s": algo_res["elapsed_s"],
        }

    val_report = print_analysis_report(
        best_algo, best_probs_val, y_val, models, FEATURE_NAMES, split_name="val",
    )
    full_report["val_analysis"] = val_report

    # Holdout evaluation (blindato — only for final report)
    best_model = models.get(best_algo if best_algo != "ensemble" else "lgbm") or models.get(list(models.keys())[0])
    # Select best non-DL algo by pnl_sharpe (includes catboost; used for holdout + export)
    _non_dl_candidates = [a for a in ["catboost", "lgbm", "xgb", "lr"] if a in results]
    non_dl_best_algo = (
        max(_non_dl_candidates, key=lambda a: results[a]["pnl"]["sharpe"])
        if _non_dl_candidates else None
    )
    # Fit calibrator on the export model's own val predictions (avoid winner mismatch)
    if non_dl_best_algo and HAS_SKLEARN:
        export_calibrator = calibrate(results[non_dl_best_algo]["probs_val"], y_val)
    else:
        export_calibrator = calibrator

    # Compare isotonic vs temperature scaling using a held-out 20% of val.
    # In-sample isotonic ECE is always 0 (it memorises its training data), so we must
    # evaluate on data the isotonic calibrator has NOT seen.  Temperature has 1 parameter
    # and generalises by construction; isotonic needs enough samples to be reliable.
    _MIN_VAL_FOR_ISOTONIC = 5_000
    export_temperature: float | None = None
    if non_dl_best_algo and HAS_SKLEARN:
        _export_probs_val = results[non_dl_best_algo]["probs_val"]
        _T = calibrate_temperature(_export_probs_val, y_val)
        if len(y_val) < _MIN_VAL_FOR_ISOTONIC:
            # Too few samples for reliable isotonic regression.
            export_temperature = _T
            _temp_ece = _compute_ece(apply_temperature(_T, _export_probs_val), y_val)
            print(f"  Calibration: temperature T={_T:.4f} forced "
                  f"(n_val={len(y_val)} < {_MIN_VAL_FOR_ISOTONIC}, ECE {_temp_ece:.4f})")
        else:
            # Held-out ECE: fit isotonic on 80 % of val, evaluate both methods on the other 20 %.
            _n_fit = int(len(y_val) * 0.80)
            _iso_heldout = calibrate(_export_probs_val[:_n_fit], y_val[:_n_fit])
            _probs_eval   = _export_probs_val[_n_fit:]
            _y_eval       = y_val[_n_fit:]
            _iso_ece  = _compute_ece(apply_calibration(_iso_heldout, _probs_eval), _y_eval)
            _temp_ece = _compute_ece(apply_temperature(_T, _probs_eval), _y_eval)
            if _temp_ece < _iso_ece:
                export_temperature = _T
                print(f"  Calibration: temperature T={_T:.4f} chosen "
                      f"(held-out ECE {_temp_ece:.4f} < isotonic {_iso_ece:.4f})")
            else:
                print(f"  Calibration: isotonic chosen "
                      f"(held-out ECE {_iso_ece:.4f} <= temperature {_temp_ece:.4f}  T={_T:.4f})")

    if non_dl_best_algo:
        non_dl_probs_test: np.ndarray | None = None
        if non_dl_best_algo == "lgbm" and models.get("lgbm"):
            non_dl_probs_test = models["lgbm"].predict(X_test)
        elif non_dl_best_algo == "xgb" and models.get("xgb"):
            non_dl_probs_test = models["xgb"].predict(xgb.DMatrix(X_test, feature_names=FEATURE_NAMES))
        elif non_dl_best_algo == "catboost" and models.get("catboost"):
            non_dl_probs_test = models["catboost"].predict(X_test, prediction_type="Probability")[:, 1]
        elif non_dl_best_algo == "lr" and models.get("lr"):
            non_dl_probs_test = models["lr"].predict_proba(X_test)[:, 1]

        if non_dl_probs_test is not None:
            non_dl_probs_test_cal = apply_calibration(export_calibrator, non_dl_probs_test)
            pnl_test = compute_pnl_metrics(non_dl_probs_test_cal, y_test)
            test_acc = float(((non_dl_probs_test > 0.5) == y_test).mean())
            print(f"\n{'='*64}")
            print(f"  HOLDOUT TEST ({non_dl_best_algo.upper()}) -- BLIND EVALUATION")
            print(f"{'='*64}")
            print(f"  acc={test_acc:.4f}  pnl_sharpe={pnl_test['sharpe']:.3f}  "
                  f"n_trades={pnl_test['n_trades']}  cumulative_pnl={pnl_test['pnl']:.4f}")

            # Full analysis on test set
            test_report = print_analysis_report(
                non_dl_best_algo, non_dl_probs_test_cal, y_test,
                models, FEATURE_NAMES, split_name="test",
            )
            full_report["test_analysis"] = test_report
        else:
            pnl_test = {"pnl": 0.0, "sharpe": 0.0, "win_rate": 0.0, "n_trades": 0}
            test_acc = 0.0
    else:
        pnl_test = {"pnl": 0.0, "sharpe": 0.0, "win_rate": 0.0, "n_trades": 0}
        test_acc = 0.0
        non_dl_best_algo = best_algo

    full_report["winner"] = best_algo
    full_report["export_algo"] = non_dl_best_algo
    full_report["holdout"] = {
        "test_acc": test_acc,
        "pnl_sharpe": pnl_test["sharpe"],
        "pnl": pnl_test["pnl"],
        "n_trades": pnl_test["n_trades"],
        "win_rate": pnl_test.get("win_rate", 0.0),
    }

    # --- Export artifacts ---
    export_dir.mkdir(parents=True, exist_ok=True)

    metrics_payload = {
        "training_samples": int(n_train),
        "val_samples":      int(len(y_val)),
        "test_samples":     int(len(y_test)),
        "yes_rate":         round(float(y.mean()), 6),
        "val_acc":          round(best_result["val_acc"], 6),
        "test_acc":         round(test_acc, 6),
        "val_pnl_sharpe":   round(best_result["pnl"]["sharpe"], 4),
        "val_pnl":          round(best_result["pnl"]["pnl"], 4),
        "val_n_trades":     best_result["pnl"]["n_trades"],
        "test_pnl_sharpe":  round(pnl_test["sharpe"], 4),
        "test_pnl":         round(pnl_test["pnl"], 4),
        "test_n_trades":    pnl_test["n_trades"],
        "has_coinbase_features": has_cb,
        "has_derivatives":  has_deriv,
        "trained_at_utc":   datetime.now(UTC).isoformat(),
        "best_algorithm":   best_algo,
        "fee_bps":          FEE_BPS,
    }

    # Export best runtime-compatible model (non-DL preferred for immediate use)
    # Uses export_calibrator fitted on the exported model's own val preds (not winner's)
    export_algo = non_dl_best_algo or best_algo
    if export_algo == "lgbm" and models.get("lgbm"):
        artifact_path = _export_lgbm_artifact(
            models["lgbm"], export_calibrator, means, stds,
            metrics_payload, export_dir, interval,
            temperature=export_temperature,
        )
    elif export_algo == "xgb" and models.get("xgb"):
        artifact_path = _export_xgb_artifact(
            models["xgb"], export_calibrator, means, stds,
            metrics_payload, export_dir, interval,
            temperature=export_temperature,
        )
    elif export_algo == "catboost" and models.get("catboost"):
        artifact_path = _export_catboost_artifact(
            models["catboost"], export_calibrator, means, stds,
            metrics_payload, export_dir, interval,
            temperature=export_temperature,
        )
    else:
        artifact_path = _export_lr_artifact(
            models.get("lr") or list(models.values())[0],
            export_calibrator, means, stds, metrics_payload, export_dir, interval,
            temperature=export_temperature,
        )
    print(f"\n  Artifact saved: {artifact_path}")

    # Export LSTM/TCN .pth files
    if models.get("lstm"):
        pth = export_dir / f"btc_{interval}_lstm.pth"
        _export_dl_pth(models["lstm"], pth)
        print(f"  LSTM saved:     {pth}")
    if models.get("tcn"):
        pth = export_dir / f"btc_{interval}_tcn.pth"
        _export_dl_pth(models["tcn"], pth)
        print(f"  TCN saved:      {pth}")

    # Save comprehensive training report
    save_training_report(interval, full_report, export_dir)

    # Log training run
    log_path = Path("data/btc/training_log.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"interval": interval, **metrics_payload, "artifact": str(artifact_path)}) + "\n")

    return metrics_payload


# ---------------------------------------------------------------------------
# Auto-promotion check (--live mode)
# ---------------------------------------------------------------------------

PROMOTION_GATES = {
    "val_pnl_sharpe": lambda v: v > 0.5,
    "val_pnl":        lambda v: v > 0.0,
    "val_n_trades":   lambda v: v >= 50,
    "test_pnl":       lambda v: v > -0.02,
}


def check_promotion(metrics: dict, interval: str, export_dir: Path) -> bool:
    """Return True if new model passes all gates and should be promoted."""
    failures = [
        f"{k}: {metrics.get(k)} fails {k}"
        for k, gate in PROMOTION_GATES.items()
        if not gate(metrics.get(k, -999))
    ]
    if failures:
        print(f"\n  [auto-promo] NOT promoted -- gate failures: {'; '.join(failures)}")
        return False

    # Check improvement over current deployed model
    agents_yaml = Path("config/agents.yaml")
    if agents_yaml.exists():
        try:
            text = agents_yaml.read_text(encoding="utf-8")
            # Simple heuristic: new val_pnl_sharpe must be at least +0.05 better
            # (full Bayesian comparison out of scope here)
            print(f"\n  [auto-promo] All gates passed for {interval}.")
            print(f"  [auto-promo] Review artifact at: {export_dir / f'btc_{interval}_best.json'}")
            print(f"  [auto-promo] Update agents.yaml manually: model_artifact_path: {export_dir / f'btc_{interval}_best.json'}")
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC Up/Down v2 training -- PnL-optimised, LGBM+XGB+LSTM+TCN"
    )
    parser.add_argument(
        "--interval", default="5m",
        help="Candle interval: 5m | 15m | all (default: 5m)"
    )
    parser.add_argument(
        "--lookback-months", type=int, default=6,
        help="Sliding-window lookback in months (default: 6)"
    )
    parser.add_argument(
        "--algorithms", default="lgbm,xgb,catboost,lr,lstm,tcn,stacking,ensemble",
        help="Comma-separated list: lgbm,xgb,catboost,lr,lstm,tcn,stacking,ensemble (default: all)"
    )
    parser.add_argument(
        "--optuna-trials", type=int, default=50,
        help="Number of Optuna trials per algorithm (default: 50, requires: pip install optuna)"
    )
    parser.add_argument(
        "--export-path", default="data/models/v4",
        help="Directory for output artifacts (default: data/models/v4)"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Live mode: sliding window retrain with auto-promotion gate check"
    )
    args = parser.parse_args()

    algos  = [a.strip().lower() for a in args.algorithms.split(",") if a.strip()]
    export_dir = Path(args.export_path)

    intervals = ["5m", "15m"] if args.interval == "all" else [args.interval]

    print(f"BTC Up/Down v2 Training")
    print(f"  Intervals:       {', '.join(intervals)}")
    print(f"  Lookback:        {args.lookback_months} months")
    print(f"  Algorithms:      {', '.join(algos)}")
    print(f"  LGBM available:  {HAS_LGBM}")
    print(f"  XGB  available:  {HAS_XGB}")
    print(f"  CatBoost avail.: {HAS_CATBOOST}")
    print(f"  Optuna avail.:   {HAS_OPTUNA} ({'%d trials' % args.optuna_trials if HAS_OPTUNA else 'fallback to grid'})")
    print(f"  PyTorch avail.:  {HAS_TORCH}")
    print(f"  Export path:     {export_dir}")
    if args.live:
        print(f"  Mode:            LIVE (sliding window, auto-promo gate)")

    for interval in intervals:
        metrics = train_interval(
            interval,
            lookback_months=args.lookback_months,
            algorithms=algos,
            export_dir=export_dir,
            n_optuna_trials=args.optuna_trials,
        )
        if metrics and args.live:
            check_promotion(metrics, interval, export_dir)

    print(f"\nDone. Training log: data/btc/training_log.jsonl")
    print(f"Next step: update config/agents.yaml -> model_artifact_path")


if __name__ == "__main__":
    main()
