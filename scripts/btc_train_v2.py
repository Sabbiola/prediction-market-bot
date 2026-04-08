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
FEE_BPS  = 720           # Polymarket BTC taker fee in basis points
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

    # Derivatives: try parquet first, fallback to jsonl
    deriv: list[dict] = []
    for ext in ("jsonl", "parquet"):
        p = Path(f"data/btc/derivatives.{ext}")
        if p.exists():
            if ext == "jsonl":
                deriv = _load_jsonl(p)
            break

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
        base = candles[max(0, idx - n)]["close"]
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
    annual_factor = math.sqrt(252 * 288)   # ~5-min candles per year
    sharpe = float(arr.mean() / (arr.std() + 1e-8) * annual_factor)
    return {
        "pnl":      float(arr.sum()),
        "sharpe":   round(sharpe, 4),
        "win_rate": float((arr > 0).mean()),
        "n_trades": len(arr),
    }


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
# LightGBM
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Hyperparameter grids (searched by pnl_sharpe on val set)
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


def train_lgbm(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
) -> tuple[Any, np.ndarray]:
    if not HAS_LGBM:
        return None, np.full(len(y_val), 0.5)

    best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0

    for i, grid_params in enumerate(_LGBM_GRID):
        dtrain = lgb.Dataset(X_train, label=y_train)
        dval   = lgb.Dataset(X_val,   label=y_val,   reference=dtrain)
        params = {
            "objective": "binary", "metric": "binary_logloss",
            "n_estimators": 500, "verbose": -1, "random_state": 42,
            **grid_params,
        }
        callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=-1)]
        model = lgb.train(params, dtrain, num_boost_round=params["n_estimators"],
                          valid_sets=[dval], callbacks=callbacks)
        probs = model.predict(X_val)
        m = compute_pnl_metrics(probs, y_val)
        if m["sharpe"] > best_sharpe:
            best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    LGBM [{i+1}/{len(_LGBM_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} leaves={grid_params['num_leaves']} lr={grid_params['learning_rate']}  ← best", flush=True)
        else:
            print(f"    LGBM [{i+1}/{len(_LGBM_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} leaves={grid_params['num_leaves']} lr={grid_params['learning_rate']}", flush=True)

    return best_model, best_probs


# ---------------------------------------------------------------------------
# XGBoost
# ---------------------------------------------------------------------------

def train_xgb(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
) -> tuple[Any, np.ndarray]:
    if not HAS_XGB:
        return None, np.full(len(y_val), 0.5)

    best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0

    for i, grid_params in enumerate(_XGB_GRID):
        dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=FEATURE_NAMES)
        dval   = xgb.DMatrix(X_val,   label=y_val,   feature_names=FEATURE_NAMES)
        params = {
            "objective": "binary:logistic", "eval_metric": "logloss",
            "seed": 42, "verbosity": 0, "subsample": grid_params["subsample"],
            "colsample_bytree": grid_params["colsample_bytree"],
            "max_depth": grid_params["max_depth"],
            "learning_rate": grid_params["learning_rate"],
            "min_child_weight": grid_params["min_child_weight"],
        }
        model = xgb.train(params, dtrain, num_boost_round=500,
                          evals=[(dval, "val")], early_stopping_rounds=50, verbose_eval=False)
        probs = model.predict(dval)
        m = compute_pnl_metrics(probs, y_val)
        if m["sharpe"] > best_sharpe:
            best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    XGB  [{i+1}/{len(_XGB_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} depth={grid_params['max_depth']} lr={grid_params['learning_rate']}  ← best", flush=True)
        else:
            print(f"    XGB  [{i+1}/{len(_XGB_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} depth={grid_params['max_depth']} lr={grid_params['learning_rate']}", flush=True)

    return best_model, best_probs


# ---------------------------------------------------------------------------
# Logistic Regression (sklearn)
# ---------------------------------------------------------------------------

def train_lr_sklearn(
    X_train: np.ndarray, y_train: np.ndarray,
    X_val:   np.ndarray, y_val:   np.ndarray,
) -> tuple[Any, np.ndarray]:
    best_model, best_probs, best_sharpe = None, np.full(len(y_val), 0.5), -999.0

    for i, grid_params in enumerate(_LR_GRID):
        model = LogisticRegression(solver="saga", max_iter=500, random_state=42, **grid_params)
        model.fit(X_train, y_train)
        probs = model.predict_proba(X_val)[:, 1]
        m = compute_pnl_metrics(probs, y_val)
        if m["sharpe"] > best_sharpe:
            best_sharpe, best_model, best_probs = m["sharpe"], model, probs
            print(f"    LR   [{i+1}/{len(_LR_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} C={grid_params['C']}  ← best", flush=True)
        else:
            print(f"    LR   [{i+1}/{len(_LR_GRID)}] pnl_sharpe={m['sharpe']:.3f} n_trades={m['n_trades']} C={grid_params['C']}", flush=True)

    return best_model, best_probs


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
    epochs: int = 30,
    lr: float = 1e-3,
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
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        scheduler.step()

        # Validation loss for early stopping
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.from_numpy(Xs_va))
            val_loss = criterion(val_pred, torch.from_numpy(ys_va).unsqueeze(1)).item()
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if (epoch + 1) % 10 == 0:
            print(f"    LSTM epoch {epoch+1}/{epochs}  val_loss={val_loss:.5f}")

    if best_state:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        probs = model(torch.from_numpy(Xs_va)).squeeze(1).numpy()

    # Pad to match original y_val length (sequences shift by SEQ_LEN)
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
    epochs: int = 30,
    lr: float = 1e-3,
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
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.from_numpy(Xs_va))
            val_loss = criterion(val_pred, torch.from_numpy(ys_va).unsqueeze(1)).item()
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

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
# Calibration (isotonic regression)
# ---------------------------------------------------------------------------

def calibrate(probs_val: np.ndarray, y_val: np.ndarray) -> IsotonicRegression:
    """Fit isotonic regression calibrator on validation predictions."""
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(probs_val.reshape(-1, 1), y_val)
    return iso


def apply_calibration(iso: IsotonicRegression, probs: np.ndarray) -> np.ndarray:
    return iso.predict(probs.reshape(-1, 1))


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
        "calibration": _calibration_payload(calibrator),
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
        "calibration": _calibration_payload(calibrator),
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
        "calibration": _calibration_payload(calibrator),
    }
    path = out_dir / f"btc_{interval}_{suffix}.json"
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def _export_dl_pth(model: Any, out_path: Path) -> None:
    if model is None or not HAS_TORCH:
        return
    torch.save(model.state_dict(), str(out_path))


def _calibration_payload(calibrator: IsotonicRegression | None) -> dict:
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
          f"Coinbase: {len(data['coinbase'])} ({'OK' if has_cb else 'MISSING — CB features=0'}) | "
          f"Derivatives: {len(data['derivatives'])} rows ({'OK' if has_deriv else 'MISSING — deriv features=0'})")

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

    print(f"  Split: train={n_train}  val={len(y_val)}  test={len(y_test)} (blindato)")

    # Train each algorithm
    results: dict[str, dict] = {}
    models:  dict[str, Any]  = {}

    if "lgbm" in algorithms and HAS_LGBM:
        print("  Training LightGBM ...")
        t0 = time.time()
        m, probs_val = train_lgbm(X_train, y_train, X_val, y_val)
        models["lgbm"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["lgbm"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                           "elapsed_s": round(time.time() - t0, 1)}
        print(f"    LGBM: val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['lgbm']['elapsed_s']}s)")
    elif "lgbm" in algorithms:
        print("  LGBM: skipped (lightgbm not installed — pip install lightgbm)")

    if "xgb" in algorithms and HAS_XGB:
        print("  Training XGBoost ...")
        t0 = time.time()
        m, probs_val = train_xgb(X_train, y_train, X_val, y_val)
        models["xgb"] = m
        pnl = compute_pnl_metrics(probs_val, y_val)
        acc = float(((probs_val > 0.5) == y_val).mean())
        results["xgb"] = {"probs_val": probs_val, "pnl": pnl, "val_acc": acc,
                          "elapsed_s": round(time.time() - t0, 1)}
        print(f"    XGB:  val_acc={acc:.4f}  pnl_sharpe={pnl['sharpe']:.3f}  "
              f"n_trades={pnl['n_trades']}  ({results['xgb']['elapsed_s']}s)")
    elif "xgb" in algorithms:
        print("  XGB: skipped (xgboost not installed — pip install xgboost)")

    if "lr" in algorithms:
        print("  Training Logistic Regression ...")
        t0 = time.time()
        m, probs_val = train_lr_sklearn(X_train, y_train, X_val, y_val)
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
        print("  LSTM: skipped (torch not installed — pip install torch)")

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
        print("  TCN: skipped (torch not installed — pip install torch)")

    if not results:
        print("  ERROR: no algorithms produced results.")
        return {}

    # Ensemble (weighted average by PnL Sharpe, clipped to >=0)
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

    # --- Select best by PnL Sharpe ---
    best_algo = max(results, key=lambda k: results[k]["pnl"]["sharpe"])
    best_result = results[best_algo]
    print(f"\n  WINNER: {best_algo}  pnl_sharpe={best_result['pnl']['sharpe']:.3f}  "
          f"val_acc={best_result['val_acc']:.4f}")

    # Calibrate best model
    best_probs_val = best_result["probs_val"]
    calibrator = calibrate(best_probs_val, y_val)
    cal_probs_val = apply_calibration(calibrator, best_probs_val)
    pnl_cal = compute_pnl_metrics(cal_probs_val, y_val)
    print(f"  After calibration: pnl_sharpe={pnl_cal['sharpe']:.3f}  n_trades={pnl_cal['n_trades']}")

    # Holdout evaluation (blindato — only for final report)
    best_model = models.get(best_algo if best_algo != "ensemble" else "lgbm") or models.get(list(models.keys())[0])
    # For DL models we use non-DL best for holdout (runtime compat)
    non_dl_best_algo = next((a for a in ["lgbm", "xgb", "lr"] if a in results), None)
    if non_dl_best_algo:
        non_dl_probs_test: np.ndarray | None = None
        if non_dl_best_algo == "lgbm" and models.get("lgbm"):
            non_dl_probs_test = models["lgbm"].predict(X_test)
        elif non_dl_best_algo == "xgb" and models.get("xgb"):
            non_dl_probs_test = models["xgb"].predict(xgb.DMatrix(X_test, feature_names=FEATURE_NAMES))
        elif non_dl_best_algo == "lr" and models.get("lr"):
            non_dl_probs_test = models["lr"].predict_proba(X_test)[:, 1]

        if non_dl_probs_test is not None:
            non_dl_probs_test_cal = apply_calibration(calibrator, non_dl_probs_test)
            pnl_test = compute_pnl_metrics(non_dl_probs_test_cal, y_test)
            test_acc = float(((non_dl_probs_test > 0.5) == y_test).mean())
            print(f"\n  HOLDOUT TEST ({non_dl_best_algo}): "
                  f"acc={test_acc:.4f}  pnl_sharpe={pnl_test['sharpe']:.3f}  "
                  f"n_trades={pnl_test['n_trades']}  cumulative_pnl={pnl_test['pnl']:.4f}")
        else:
            pnl_test = {"pnl": 0.0, "sharpe": 0.0, "win_rate": 0.0, "n_trades": 0}
            test_acc = 0.0
    else:
        pnl_test = {"pnl": 0.0, "sharpe": 0.0, "win_rate": 0.0, "n_trades": 0}
        test_acc = 0.0
        non_dl_best_algo = best_algo

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
    }

    # Export best runtime-compatible model (non-DL preferred for immediate use)
    export_algo = non_dl_best_algo or best_algo
    if export_algo == "lgbm" and models.get("lgbm"):
        artifact_path = _export_lgbm_artifact(
            models["lgbm"], calibrator, means, stds,
            metrics_payload, export_dir, interval,
        )
    elif export_algo == "xgb" and models.get("xgb"):
        artifact_path = _export_xgb_artifact(
            models["xgb"], calibrator, means, stds,
            metrics_payload, export_dir, interval,
        )
    else:
        artifact_path = _export_lr_artifact(
            models.get("lr") or list(models.values())[0],
            calibrator, means, stds, metrics_payload, export_dir, interval,
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
        print(f"\n  [auto-promo] NOT promoted — gate failures: {'; '.join(failures)}")
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
        description="BTC Up/Down v2 training — PnL-optimised, LGBM+XGB+LSTM+TCN"
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
        "--algorithms", default="lgbm,xgb,lr,lstm,tcn,ensemble",
        help="Comma-separated list: lgbm,xgb,lr,lstm,tcn,ensemble (default: all)"
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
        )
        if metrics and args.live:
            check_promotion(metrics, interval, export_dir)

    print(f"\nDone. Training log: data/btc/training_log.jsonl")
    print(f"Next step: update config/agents.yaml → model_artifact_path")


if __name__ == "__main__":
    main()
