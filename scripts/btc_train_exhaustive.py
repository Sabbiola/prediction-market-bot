"""Exhaustive BTC Up/Down model search — any Binance interval.

Tries all combinations of:
  - Feature configs (15 curated sets with different indicators and lookbacks)
  - Regularisation (L1/L2/ElasticNet, C in [0.001..100])
  - 3-fold walk-forward cross-validation

Usage:
    # Fetch base 5m candles first (needed for all intervals):
    python scripts/btc_fetch_ohlcv.py --months 24

    # Train on 5m only (~13 min):
    python scripts/btc_train_exhaustive.py --interval 5m

    # Train on 15m only (~13 min):
    python scripts/btc_train_exhaustive.py --interval 15m

    # Train on both 5m and 15m (~26 min, default):
    python scripts/btc_train_exhaustive.py

    # Train on any resampleable interval (30m, 1h, 4h):
    python scripts/btc_train_exhaustive.py --interval 30m
    python scripts/btc_train_exhaustive.py --interval 1h

    # Use native fetched candles instead of resampling (if available):
    python scripts/btc_fetch_ohlcv.py --interval 15m
    python scripts/btc_train_exhaustive.py --interval 15m
    # (auto-detects data/btc/ohlcv_15m.jsonl and uses it directly)

Outputs (per interval):
    data/models/v2/btc_updown_{interval}_best.json
    data/models/v2/grid_search_results_{interval}.json
"""
from __future__ import annotations

import argparse
import json
import math
import time
import warnings
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

# sklearn 1.8 deprecated penalty/n_jobs in LogisticRegression
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

# ---------------------------------------------------------------------------
# Paths and interval config
# ---------------------------------------------------------------------------
BASE_OHLCV_PATH = Path("data/btc/ohlcv_5m.jsonl")
OUT_DIR = Path("data/models/v2")

# How many 5m candles make up each target interval
_INTERVAL_FACTOR: dict[str, int] = {
    "5m":  1,
    "15m": 3,
    "30m": 6,
    "1h":  12,
    "2h":  24,
    "4h":  48,
}

SUPPORTED_INTERVALS = list(_INTERVAL_FACTOR.keys())


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def resample_candles(candles_5m: list[dict], factor: int) -> list[dict]:
    """Aggregate consecutive 5m candles by `factor` into a coarser interval."""
    if factor == 1:
        return candles_5m
    out = []
    i = 0
    needed = factor
    while i + needed - 1 < len(candles_5m):
        group = candles_5m[i:i + needed]
        out.append({
            "open_time_ms": group[0]["open_time_ms"],
            "open":   group[0]["open"],
            "high":   max(c["high"] for c in group),
            "low":    min(c["low"]  for c in group),
            "close":  group[-1]["close"],
            "volume": sum(c["volume"] for c in group),
        })
        i += needed
    return out


def load_or_resample(interval: str, candles_5m: list[dict]) -> list[dict]:
    """Load native candle file for `interval` if it exists, else resample from 5m."""
    native_path = Path(f"data/btc/ohlcv_{interval}.jsonl")
    if interval != "5m" and native_path.exists():
        print(f"  Using native {interval} candles from {native_path}")
        candles: list[dict] = []
        with native_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    candles.append(json.loads(line))
        candles.sort(key=lambda c: c["open_time_ms"])
        return candles
    factor = _INTERVAL_FACTOR[interval]
    if factor == 1:
        return candles_5m
    resampled = resample_candles(candles_5m, factor)
    print(f"  Resampled 5m -> {interval}: {len(resampled):,} candles (factor={factor})")
    return resampled


# ---------------------------------------------------------------------------
# Feature computation (vectorised with numpy)
# ---------------------------------------------------------------------------

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average (past-only, no look-ahead)."""
    alpha = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi_series(closes: np.ndarray, period: int) -> np.ndarray:
    """RSI series normalised to [0,1]. Requires period+1 warm-up candles."""
    n = len(closes)
    rsi = np.full(n, 0.5)
    deltas = np.diff(closes, prepend=closes[0])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    for i in range(period, n):
        avg_g = gains[i - period + 1:i + 1].mean()
        avg_l = losses[i - period + 1:i + 1].mean()
        if avg_l < 1e-10:
            rsi[i] = 1.0
        else:
            rs = avg_g / avg_l
            rsi[i] = rs / (1.0 + rs)
    return rsi


def build_features(
    candles: list[dict],
    *,
    return_lags: list[int],
    rsi_period: int,
    bb_window: int,
    vol_ma_window: int,
    volatility_window: int,
    use_hour: bool,
    use_weekday: bool,
    use_macd: bool,
    use_stochastic: bool,
    use_atr: bool,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Compute feature matrix X and label vector y for all valid candles.

    Returns (X, y, feature_names).
    Label: 1 if candle.close > candle.open (UP), else 0.
    Features are computed from PRIOR completed candles only (no leakage).
    """
    n = len(candles)
    opens  = np.array([c["open"]   for c in candles])
    highs  = np.array([c["high"]   for c in candles])
    lows   = np.array([c["low"]    for c in candles])
    closes = np.array([c["close"]  for c in candles])
    vols   = np.array([c["volume"] for c in candles])
    times  = np.array([c["open_time_ms"] for c in candles])

    # Pre-compute series
    rsi_series = _rsi_series(closes, rsi_period)

    # MACD: EMA(12) - EMA(26) on closes, normalised by price
    if use_macd:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = (ema12 - ema26) / np.maximum(closes, 1e-8)

    required_warmup = max(
        max(return_lags, default=1),
        rsi_period + 1,
        bb_window,
        vol_ma_window,
        volatility_window,
        26 if use_macd else 1,
        14 if use_stochastic else 1,
    ) + 2

    rows_X: list[list[float]] = []
    rows_y: list[int] = []

    for idx in range(required_warmup, n - 1):
        # Label: current candle direction (computed from CURRENT candle's OHLC)
        c = candles[idx]
        label = 1 if c["close"] > c["open"] else 0

        # All features use only candles[:idx] (prior data)
        feats: list[float] = []
        feat_names_proto: list[str] = []

        # -- Returns from prev close-to-prev-close
        prev_close = closes[idx - 1]
        for lag in return_lags:
            base = closes[max(0, idx - lag)]
            ret = (prev_close - base) / max(base, 1e-8)
            feats.append(float(np.clip(ret, -0.30, 0.30)))
            feat_names_proto.append(f"f_btc_return_{lag}c")

        # -- RSI (using value at idx-1, i.e. prior candle)
        rsi_val = rsi_series[idx - 1]
        feats.append(float(np.clip((rsi_val - 0.5) * 2.0, -1.0, 1.0)))
        feat_names_proto.append(f"f_btc_rsi_{rsi_period}")

        # -- Volume ratio (log)
        vol_slice = vols[max(0, idx - vol_ma_window): idx]
        avg_vol = vol_slice.mean() if len(vol_slice) > 0 else vols[idx - 1]
        vol_ratio = math.log(max(vols[idx - 1], 1e-8) / max(avg_vol, 1e-8))
        feats.append(float(np.clip(vol_ratio, -3.0, 3.0)))
        feat_names_proto.append(f"f_btc_vol_ratio_ma{vol_ma_window}")

        # -- Realized volatility
        cl_slice = closes[max(0, idx - volatility_window): idx]
        if len(cl_slice) >= 2:
            rets_slice = np.diff(cl_slice) / np.maximum(cl_slice[:-1], 1e-8)
            f_vol = float(np.clip(rets_slice.std() * 100, 0.0, 5.0))
        else:
            f_vol = 0.0
        feats.append(f_vol)
        feat_names_proto.append(f"f_btc_volatility_{volatility_window}c")

        # -- Bollinger Band position (centred at 0)
        bb_slice = closes[max(0, idx - bb_window): idx]
        if len(bb_slice) >= 2:
            bb_mean = bb_slice.mean()
            bb_std  = bb_slice.std()
            upper = bb_mean + 2 * bb_std
            lower = bb_mean - 2 * bb_std
            band_w = max(upper - lower, 1e-8)
            bb_pos = float(np.clip((closes[idx - 1] - lower) / band_w, 0.0, 1.0)) - 0.5
        else:
            bb_pos = 0.0
        feats.append(bb_pos)
        feat_names_proto.append(f"f_btc_bb_pos_{bb_window}")

        # -- Time features
        if use_hour:
            ts_sec = times[idx] / 1000
            hour_frac = (ts_sec % 86400) / 3600
            feats.append(math.sin(2 * math.pi * hour_frac / 24))
            feats.append(math.cos(2 * math.pi * hour_frac / 24))
            feat_names_proto += ["f_decision_hour_utc_sin", "f_decision_hour_utc_cos"]

        if use_weekday:
            ts_sec = times[idx] / 1000
            day_of_week = (int(ts_sec // 86400) + 3) % 7  # 0=Mon
            feats.append(math.sin(2 * math.pi * day_of_week / 7))
            feats.append(math.cos(2 * math.pi * day_of_week / 7))
            feat_names_proto += ["f_decision_weekday_sin", "f_decision_weekday_cos"]

        # -- MACD signal
        if use_macd:
            feats.append(float(np.clip(macd_line[idx - 1], -0.05, 0.05)))
            feat_names_proto.append("f_btc_macd")

        # -- Stochastic %K (14-period)
        if use_stochastic:
            stoch_window = 14
            lo14 = lows[max(0, idx - stoch_window): idx].min()
            hi14 = highs[max(0, idx - stoch_window): idx].max()
            rng14 = max(hi14 - lo14, 1e-8)
            stoch_k = float(np.clip((closes[idx - 1] - lo14) / rng14, 0.0, 1.0)) - 0.5
            feats.append(stoch_k)
            feat_names_proto.append("f_btc_stochastic_k")

        # -- ATR ratio
        if use_atr:
            atr_window = 14
            tr_slice: list[float] = []
            for k in range(max(1, idx - atr_window), idx):
                tr = max(
                    highs[k] - lows[k],
                    abs(highs[k] - closes[k - 1]),
                    abs(lows[k]  - closes[k - 1]),
                )
                tr_slice.append(tr)
            atr = np.mean(tr_slice) if tr_slice else 1e-8
            atr_ratio = float(np.clip(atr / max(closes[idx - 1], 1e-8) * 100, 0.0, 5.0))
            feats.append(atr_ratio)
            feat_names_proto.append("f_btc_atr_ratio")

        rows_X.append(feats)
        rows_y.append(label)

    X = np.array(rows_X, dtype=np.float64)
    y = np.array(rows_y, dtype=np.int32)
    return X, y, feat_names_proto


# ---------------------------------------------------------------------------
# Feature configs to search
# ---------------------------------------------------------------------------
# Each entry is a dict of kwargs passed to build_features().
# We define curated sets covering different hypotheses about BTC 5m/15m dynamics.

FEATURE_CONFIGS: list[dict[str, Any]] = [
    # 1. Baseline (same as v1)
    {
        "name": "baseline_v1",
        "return_lags": [1, 3, 12, 48],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": False, "use_atr": False,
    },
    # 2. Short mean-reversion focus
    {
        "name": "mean_reversion_short",
        "return_lags": [1, 3, 6],
        "rsi_period": 7, "bb_window": 10, "vol_ma_window": 10,
        "volatility_window": 6, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": True, "use_atr": False,
    },
    # 3. Long momentum focus
    {
        "name": "momentum_long",
        "return_lags": [12, 24, 48],
        "rsi_period": 21, "bb_window": 30, "vol_ma_window": 20,
        "volatility_window": 24, "use_hour": True, "use_weekday": True,
        "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    # 4. Full kitchen-sink (all features)
    {
        "name": "kitchen_sink",
        "return_lags": [1, 3, 6, 12, 24, 48],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": True,
        "use_macd": True, "use_stochastic": True, "use_atr": True,
    },
    # 5. Volume + volatility emphasis
    {
        "name": "volume_vol",
        "return_lags": [1, 3, 12],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 10,
        "volatility_window": 6, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": False, "use_atr": True,
    },
    # 6. RSI-7 + BB tight
    {
        "name": "rsi7_bb10",
        "return_lags": [1, 3],
        "rsi_period": 7, "bb_window": 10, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": False, "use_atr": False,
    },
    # 7. MACD + RSI-21
    {
        "name": "macd_rsi21",
        "return_lags": [3, 12, 48],
        "rsi_period": 21, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": False,
        "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    # 8. Short + weekday cycles
    {
        "name": "short_weekly_cycle",
        "return_lags": [1, 3, 6],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": True,
        "use_macd": False, "use_stochastic": False, "use_atr": False,
    },
    # 9. Medium-term with Stochastic
    {
        "name": "medium_stochastic",
        "return_lags": [3, 6, 12, 24],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": True, "use_atr": True,
    },
    # 10. Tight BB + ATR (breakout detection)
    {
        "name": "breakout",
        "return_lags": [1, 3, 6, 12],
        "rsi_period": 14, "bb_window": 10, "vol_ma_window": 10,
        "volatility_window": 6, "use_hour": False, "use_weekday": False,
        "use_macd": False, "use_stochastic": False, "use_atr": True,
    },
    # 11. Wide lookbacks (4h + 8h context)
    {
        "name": "wide_lookback",
        "return_lags": [1, 12, 24, 48, 96],
        "rsi_period": 14, "bb_window": 48, "vol_ma_window": 48,
        "volatility_window": 48, "use_hour": True, "use_weekday": True,
        "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    # 12. Micro-structure (1c + 3c only, tight)
    {
        "name": "micro_structure",
        "return_lags": [1, 3],
        "rsi_period": 7, "bb_window": 10, "vol_ma_window": 10,
        "volatility_window": 6, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": True, "use_atr": True,
    },
    # 13. All extras, no returns
    {
        "name": "indicators_only",
        "return_lags": [],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": True,
        "use_macd": True, "use_stochastic": True, "use_atr": True,
    },
    # 14. Compact + MACD
    {
        "name": "compact_macd",
        "return_lags": [1, 6, 24],
        "rsi_period": 14, "bb_window": 20, "vol_ma_window": 20,
        "volatility_window": 12, "use_hour": True, "use_weekday": False,
        "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    # 15. RSI-7 + Stochastic + ATR (mean-reversion + vol)
    {
        "name": "reversal_signals",
        "return_lags": [1, 3, 6],
        "rsi_period": 7, "bb_window": 14, "vol_ma_window": 14,
        "volatility_window": 8, "use_hour": True, "use_weekday": False,
        "use_macd": False, "use_stochastic": True, "use_atr": True,
    },
]

# ---------------------------------------------------------------------------
# Hyperparameter grid
# ---------------------------------------------------------------------------
# LogisticRegression with saga solver supports both L1 and L2.
# C = inverse regularisation strength (higher = less regularisation).

HP_GRID: list[dict[str, Any]] = [
    {"l1_ratio": ratio, "penalty_name": name, "C": c}
    for (ratio, name), c in product(
        [(0.0, "l2"), (0.5, "elasticnet"), (1.0, "l1")],
        [0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 10.0, 100.0],
    )
]  # 24 combos (L1, L2, ElasticNet x 8 C values)


# ---------------------------------------------------------------------------
# Training + evaluation
# ---------------------------------------------------------------------------

def fit_and_evaluate(
    X: np.ndarray,
    y: np.ndarray,
    *,
    penalty: float,   # l1_ratio: 0.0=L2, 0.5=ElasticNet, 1.0=L1
    C: float,
    n_splits: int = 3,
) -> dict[str, float]:
    """Walk-forward CV. Returns mean metrics across folds."""
    tscv = TimeSeriesSplit(n_splits=n_splits)
    accs, briors, aucs, loglosses = [], [], [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X[train_idx], X[val_idx]
        y_tr, y_va = y[train_idx], y[val_idx]

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr)
        X_va_s = scaler.transform(X_va)

        clf = LogisticRegression(
            l1_ratio=penalty,
            C=C,
            solver="saga",
            max_iter=500,
            random_state=42,
        )
        clf.fit(X_tr_s, y_tr)

        proba = clf.predict_proba(X_va_s)[:, 1]
        preds = (proba >= 0.5).astype(int)

        accs.append(float((preds == y_va).mean()))
        briors.append(float(brier_score_loss(y_va, proba)))
        aucs.append(float(roc_auc_score(y_va, proba)))
        loglosses.append(float(log_loss(y_va, proba)))

    return {
        "val_acc":       round(float(np.mean(accs)), 6),
        "val_acc_std":   round(float(np.std(accs)), 6),
        "val_brier":     round(float(np.mean(briors)), 6),
        "val_auc":       round(float(np.mean(aucs)), 6),
        "val_logloss":   round(float(np.mean(loglosses)), 6),
    }


def retrain_full(
    X: np.ndarray,
    y: np.ndarray,
    *,
    penalty: float,   # l1_ratio: 0.0=L2, 0.5=ElasticNet, 1.0=L1
    C: float,
) -> tuple[StandardScaler, LogisticRegression]:
    """Retrain on the full dataset with the best config."""
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    clf = LogisticRegression(
        l1_ratio=penalty, C=C, solver="saga",
        max_iter=1000, random_state=42,
    )
    clf.fit(X_s, y)
    return scaler, clf


# ---------------------------------------------------------------------------
# Artifact builder
# ---------------------------------------------------------------------------

def build_artifact(
    *,
    timeframe: str,
    feature_config: dict[str, Any],
    hp: dict[str, Any],
    cv_metrics: dict[str, float],
    scaler: StandardScaler,
    clf: LogisticRegression,
    feature_names: list[str],
    n_samples: int,
    yes_rate: float,
    train_acc: float,
) -> dict[str, Any]:
    means = scaler.mean_.tolist()
    stds  = scaler.scale_.tolist()
    weights = clf.coef_[0].tolist()
    bias    = float(clf.intercept_[0])

    return {
        "artifact_type":           "prediction_model_v2",
        "model_name":              f"btc_updown_{timeframe}_lr_v2",
        "model_version":           "v2.0.0",
        "feature_schema_version":  f"btc-{timeframe}",
        "description": (
            f"Logistic regression for Polymarket BTC Up/Down {timeframe.upper()} markets. "
            f"Config: {feature_config['name']} | penalty={hp['penalty_name']} C={hp['C']} | "
            f"val_acc={cv_metrics['val_acc']:.4f} val_auc={cv_metrics['val_auc']:.4f}"
        ),
        "training_samples":  n_samples,
        "yes_rate":          round(yes_rate, 6),
        "train_accuracy":    round(train_acc, 6),
        "val_accuracy":      cv_metrics["val_acc"],
        "val_accuracy_std":  cv_metrics["val_acc_std"],
        "val_brier":         cv_metrics["val_brier"],
        "val_auc":           cv_metrics["val_auc"],
        "val_logloss":       cv_metrics["val_logloss"],
        "feature_columns":   feature_names,
        "required_features": feature_names[:2],
        "feature_config":    {k: v for k, v in feature_config.items() if k != "name"},
        "hyperparameters":   hp,
        "algorithm_payload": {
            "algorithm":    "logistic_regression",
            "feature_names": feature_names,
            "means":         [round(m, 8) for m in means],
            "stds":          [round(s, 8) for s in stds],
            "weights":       [round(w, 8) for w in weights],
            "bias":          round(bias, 8),
        },
        "calibration": {
            "method":               "none",
            "calibration_version":  "none",
            "parameters":           {},
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_search(
    candles: list[dict],
    *,
    timeframe: str,
    n_cv_splits: int = 3,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run full grid search. Returns (best_artifact, all_results)."""
    total_configs = len(FEATURE_CONFIGS) * len(HP_GRID)
    print(f"\n{'='*60}")
    print(f"  Timeframe: {timeframe.upper()}  |  Candles: {len(candles):,}")
    print(f"  Grid: {len(FEATURE_CONFIGS)} feature configs x {len(HP_GRID)} HP combos = {total_configs} runs")
    print(f"  Walk-forward folds: {n_cv_splits}")
    print(f"{'='*60}")

    all_results: list[dict[str, Any]] = []
    best_auc = -1.0
    best_result: dict[str, Any] | None = None

    run_idx = 0
    t0_total = time.time()

    for fc in FEATURE_CONFIGS:
        t0_feat = time.time()
        try:
            X, y, feat_names = build_features(
                candles,
                return_lags=fc["return_lags"],
                rsi_period=fc["rsi_period"],
                bb_window=fc["bb_window"],
                vol_ma_window=fc["vol_ma_window"],
                volatility_window=fc["volatility_window"],
                use_hour=fc["use_hour"],
                use_weekday=fc["use_weekday"],
                use_macd=fc["use_macd"],
                use_stochastic=fc["use_stochastic"],
                use_atr=fc["use_atr"],
            )
        except Exception as exc:
            print(f"  [SKIP] {fc['name']}: feature build error: {exc}")
            continue

        if len(X) < 500:
            print(f"  [SKIP] {fc['name']}: not enough samples ({len(X)})")
            continue

        yes_rate = float(y.mean())
        feat_build_ms = int((time.time() - t0_feat) * 1000)
        print(f"\n  Config [{fc['name']}]  samples={len(X):,}  features={X.shape[1]}  "
              f"yes_rate={yes_rate:.3f}  built={feat_build_ms}ms")

        for hp in HP_GRID:
            run_idx += 1
            t0_hp = time.time()
            try:
                metrics = fit_and_evaluate(X, y, penalty=hp["l1_ratio"], C=hp["C"], n_splits=n_cv_splits)
            except Exception as exc:
                print(f"    [{run_idx:>3}/{total_configs}] {hp} ERROR: {exc}")
                continue

            elapsed = time.time() - t0_hp
            marker = " <-- BEST" if metrics["val_auc"] > best_auc else ""
            print(
                f"    [{run_idx:>3}/{total_configs}]  "
                f"pen={hp['penalty_name']:>11}  C={hp['C']:>7}  "
                f"acc={metrics['val_acc']:.4f}(±{metrics['val_acc_std']:.4f})  "
                f"auc={metrics['val_auc']:.4f}  "
                f"brier={metrics['val_brier']:.4f}  "
                f"{elapsed:.1f}s{marker}"
            )

            result_entry: dict[str, Any] = {
                "timeframe": timeframe,
                "feature_config": fc["name"],
                "penalty":  hp["penalty_name"],
                "l1_ratio": hp["l1_ratio"],
                "C":        hp["C"],
                "n_features": X.shape[1],
                "n_samples":  len(X),
                "yes_rate":   yes_rate,
                **metrics,
            }
            all_results.append(result_entry)

            if metrics["val_auc"] > best_auc:
                best_auc = metrics["val_auc"]
                best_result = {
                    "X": X, "y": y, "feat_names": feat_names,
                    "fc": fc, "hp": hp, "metrics": metrics, "yes_rate": yes_rate,
                }

    elapsed_total = time.time() - t0_total
    print(f"\n  Search complete in {elapsed_total/60:.1f} min")

    if best_result is None:
        raise RuntimeError("No valid results found — check input data.")

    # Retrain best config on full data
    print(f"\n  Retraining best config on full data ...")
    print(f"    feature_config={best_result['fc']['name']}  "
          f"penalty={best_result['hp']['penalty_name']}  C={best_result['hp']['C']}")

    X_best, y_best = best_result["X"], best_result["y"]
    scaler, clf = retrain_full(X_best, y_best, penalty=best_result["hp"]["l1_ratio"], C=best_result["hp"]["C"])

    X_s = scaler.transform(X_best)
    train_acc = float((clf.predict(X_s) == y_best).mean())
    print(f"    train_acc={train_acc:.4f}  val_auc(cv)={best_auc:.4f}")

    # Feature weights summary
    print(f"\n  Feature weights (top by |weight|):")
    coef = clf.coef_[0]
    ranked = sorted(zip(best_result["feat_names"], coef), key=lambda x: abs(x[1]), reverse=True)
    for fname, w in ranked[:12]:
        print(f"    {fname:<40}  w={w:+.6f}")

    artifact = build_artifact(
        timeframe=timeframe,
        feature_config=best_result["fc"],
        hp=best_result["hp"],
        cv_metrics=best_result["metrics"],
        scaler=scaler,
        clf=clf,
        feature_names=best_result["feat_names"],
        n_samples=len(y_best),
        yes_rate=best_result["yes_rate"],
        train_acc=train_acc,
    )
    return artifact, all_results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exhaustive BTC model grid search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--interval",
        default="all",
        help=(
            f"Interval to train on. Options: {', '.join(SUPPORTED_INTERVALS)}, all. "
            "Default: all (5m + 15m). Use 'all' to run every supported interval."
        ),
    )
    parser.add_argument(
        "--cv-splits", type=int, default=3,
        help="Number of walk-forward CV folds (default: 3)",
    )
    args = parser.parse_args()

    interval_arg = args.interval.strip().lower()
    if interval_arg == "all":
        intervals_to_run = ["5m", "15m"]
    elif interval_arg in _INTERVAL_FACTOR:
        intervals_to_run = [interval_arg]
    else:
        print(f"ERROR: unsupported interval '{interval_arg}'. Choose from: {', '.join(SUPPORTED_INTERVALS)}, all")
        return

    if not BASE_OHLCV_PATH.exists():
        print(f"ERROR: {BASE_OHLCV_PATH} not found. Run scripts/btc_fetch_ohlcv.py first.")
        return

    print(f"Loading base 5m candles from {BASE_OHLCV_PATH} ...")
    candles_5m: list[dict] = []
    with BASE_OHLCV_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                candles_5m.append(json.loads(line))
    candles_5m.sort(key=lambda c: c["open_time_ms"])
    start_dt = candles_5m[0]["open_time_ms"] // 1000
    end_dt   = candles_5m[-1]["open_time_ms"] // 1000
    print(f"  Loaded {len(candles_5m):,} 5m candles  ({start_dt} to {end_dt})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_artifacts: list[tuple[str, dict[str, Any]]] = []

    for interval in intervals_to_run:
        candles = load_or_resample(interval, candles_5m)
        artifact, results = run_search(candles, timeframe=interval, n_cv_splits=args.cv_splits)

        out_path = OUT_DIR / f"btc_updown_{interval}_best.json"
        out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(f"\n  [{interval}] Artifact saved: {out_path}")

        results_path = OUT_DIR / f"grid_search_results_{interval}.json"
        results.sort(key=lambda r: r.get("val_auc", 0.0), reverse=True)
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  [{interval}] Grid results saved: {results_path}  ({len(results)} entries)")

        all_artifacts.append((interval, artifact))

    # --- Summary ---
    print("\n" + "="*60)
    print("  FINAL SUMMARY")
    print("="*60)
    for interval, artifact in all_artifacts:
        fc = artifact.get("feature_config", {})
        fc_name = fc.get("name", "?") if isinstance(fc, dict) else "?"
        print(
            f"  [{interval:>3}]  config={fc_name:<22}"
            f"  features={len(artifact['feature_columns'])}"
            f"  val_acc={artifact['val_accuracy']:.4f}(±{artifact['val_accuracy_std']:.4f})"
            f"  val_auc={artifact['val_auc']:.4f}"
            f"  brier={artifact['val_brier']:.4f}"
        )

    print("\nNext steps:")
    for interval, artifact in all_artifacts:
        schema = artifact["feature_schema_version"]
        path   = OUT_DIR / f"btc_updown_{interval}_best.json"
        print(f"  [{interval}] config/agents.yaml -> model_artifact_path: {path}")
        print(f"  [{interval}]                    -> feature_schema_version: {schema}")


if __name__ == "__main__":
    main()
