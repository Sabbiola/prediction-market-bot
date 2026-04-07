"""Exhaustive BTC Up/Down model search — XGBoost + Logistic Regression.

Grid search over:
  - 2 model types: XGBoost (non-linear) + Logistic Regression
  - 18 feature configs (OHLCV technicals + derivatives: funding rate, LS ratio, taker ratio)
  - Multiple hyperparameter combos per model type
  - 3-fold walk-forward cross-validation

Usage:
    # 1. Fetch base data:
    python scripts/btc_fetch_ohlcv.py --months 24
    python scripts/btc_fetch_derivatives.py --months 24

    # 2. Train on 5m only (~15 min):
    python scripts/btc_train_exhaustive.py --interval 5m

    # 3. Train on 15m:
    python scripts/btc_train_exhaustive.py --interval 15m

    # 4. Train all (default):
    python scripts/btc_train_exhaustive.py

Outputs (per interval):
    data/models/v3/btc_updown_{interval}_best.json       (artifact)
    data/models/v3/btc_updown_{interval}_xgb_model.json  (XGBoost native, if XGB wins)
    data/models/v3/grid_search_results_{interval}.json    (full ranking)
"""
from __future__ import annotations

import argparse
import json
import math
import time
import warnings
from bisect import bisect_right
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

# ---------------------------------------------------------------------------
# Paths and interval config
# ---------------------------------------------------------------------------
BASE_OHLCV_PATH = Path("data/btc/ohlcv_5m.jsonl")
FUNDING_PATH    = Path("data/btc/funding_rate.jsonl")
LS_RATIO_PATH   = Path("data/btc/long_short_ratio.jsonl")
TAKER_PATH      = Path("data/btc/taker_ratio.jsonl")
OUT_DIR = Path("data/models/v3")

_INTERVAL_FACTOR: dict[str, int] = {
    "5m": 1, "15m": 3, "30m": 6, "1h": 12, "2h": 24, "4h": 48,
}
SUPPORTED_INTERVALS = list(_INTERVAL_FACTOR.keys())


# ---------------------------------------------------------------------------
# Resampling
# ---------------------------------------------------------------------------

def resample_candles(candles_5m: list[dict], factor: int) -> list[dict]:
    if factor == 1:
        return candles_5m
    out = []
    i = 0
    while i + factor - 1 < len(candles_5m):
        group = candles_5m[i:i + factor]
        out.append({
            "open_time_ms": group[0]["open_time_ms"],
            "open":   group[0]["open"],
            "high":   max(c["high"] for c in group),
            "low":    min(c["low"]  for c in group),
            "close":  group[-1]["close"],
            "volume": sum(c["volume"] for c in group),
        })
        i += factor
    return out


def load_or_resample(interval: str, candles_5m: list[dict]) -> list[dict]:
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
# Derivatives data loading + alignment
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["timestamp_ms"])
    return rows


def _align_series(candle_times_ms: np.ndarray, series: list[dict], value_key: str) -> np.ndarray:
    """For each candle, find the most recent value from `series` (by timestamp).

    Returns array of same length as candle_times_ms, with np.nan where no data.
    """
    n = len(candle_times_ms)
    result = np.full(n, np.nan)
    if not series:
        return result
    ts = [r["timestamp_ms"] for r in series]
    vals = [r[value_key] for r in series]
    for i in range(n):
        idx = bisect_right(ts, candle_times_ms[i]) - 1
        if idx >= 0:
            result[i] = vals[idx]
    return result


class DerivativesData:
    """Holds pre-aligned derivatives arrays for fast feature building."""

    def __init__(self, candle_times_ms: np.ndarray) -> None:
        self.has_data = False
        funding_raw = _load_jsonl(FUNDING_PATH)
        ls_raw      = _load_jsonl(LS_RATIO_PATH)
        taker_raw   = _load_jsonl(TAKER_PATH)

        if not funding_raw and not ls_raw and not taker_raw:
            print("  [derivatives] No derivatives data found, skipping")
            self.funding_rate  = np.full(len(candle_times_ms), np.nan)
            self.ls_ratio      = np.full(len(candle_times_ms), np.nan)
            self.taker_ratio   = np.full(len(candle_times_ms), np.nan)
            return

        self.funding_rate = _align_series(candle_times_ms, funding_raw, "funding_rate")
        self.ls_ratio     = _align_series(candle_times_ms, ls_raw,     "long_short_ratio")
        self.taker_ratio  = _align_series(candle_times_ms, taker_raw,  "buy_sell_ratio")

        fr_valid = int(np.isfinite(self.funding_rate).sum())
        ls_valid = int(np.isfinite(self.ls_ratio).sum())
        tk_valid = int(np.isfinite(self.taker_ratio).sum())
        total = len(candle_times_ms)
        self.has_data = (fr_valid + ls_valid + tk_valid) > 0
        print(f"  [derivatives] Aligned: funding={fr_valid}/{total}  ls_ratio={ls_valid}/{total}  taker={tk_valid}/{total}")


# ---------------------------------------------------------------------------
# Feature computation (vectorised with numpy)
# ---------------------------------------------------------------------------

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi_series(closes: np.ndarray, period: int) -> np.ndarray:
    n = len(closes)
    rsi = np.full(n, 0.5)
    deltas = np.diff(closes, prepend=closes[0])
    gains = np.where(deltas > 0, deltas, 0.0)
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
    derivatives: DerivativesData | None,
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
    use_derivatives: bool,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Compute feature matrix X and label vector y.

    Label: 1 if candle.close > candle.open (UP), else 0.
    Features use PRIOR completed candles only (no leakage).
    """
    n = len(candles)
    opens  = np.array([c["open"]   for c in candles])
    highs  = np.array([c["high"]   for c in candles])
    lows   = np.array([c["low"]    for c in candles])
    closes = np.array([c["close"]  for c in candles])
    vols   = np.array([c["volume"] for c in candles])
    times  = np.array([c["open_time_ms"] for c in candles])

    rsi_arr = _rsi_series(closes, rsi_period)
    macd_line = None
    if use_macd:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line = (ema12 - ema26) / np.maximum(closes, 1e-8)

    required_warmup = max(
        max(return_lags, default=1), rsi_period + 1, bb_window,
        vol_ma_window, volatility_window, 26 if use_macd else 1,
        14 if use_stochastic else 1,
    ) + 2

    rows_X: list[list[float]] = []
    rows_y: list[int] = []
    feat_names: list[str] = []
    first_row = True

    for idx in range(required_warmup, n - 1):
        c = candles[idx]
        label = 1 if c["close"] > c["open"] else 0

        feats: list[float] = []
        names: list[str] = []

        # Returns
        prev_close = closes[idx - 1]
        for lag in return_lags:
            base = closes[max(0, idx - lag)]
            ret = (prev_close - base) / max(base, 1e-8)
            feats.append(float(np.clip(ret, -0.30, 0.30)))
            names.append(f"f_btc_return_{lag}c")

        # RSI
        rsi_val = rsi_arr[idx - 1]
        feats.append(float(np.clip((rsi_val - 0.5) * 2.0, -1.0, 1.0)))
        names.append(f"f_btc_rsi_{rsi_period}")

        # Volume ratio
        vol_slice = vols[max(0, idx - vol_ma_window):idx]
        avg_vol = vol_slice.mean() if len(vol_slice) > 0 else vols[idx - 1]
        feats.append(float(np.clip(math.log(max(vols[idx - 1], 1e-8) / max(avg_vol, 1e-8)), -3.0, 3.0)))
        names.append(f"f_btc_vol_ratio_ma{vol_ma_window}")

        # Realized volatility
        cl_slice = closes[max(0, idx - volatility_window):idx]
        if len(cl_slice) >= 2:
            rets_s = np.diff(cl_slice) / np.maximum(cl_slice[:-1], 1e-8)
            f_vol = float(np.clip(rets_s.std() * 100, 0.0, 5.0))
        else:
            f_vol = 0.0
        feats.append(f_vol)
        names.append(f"f_btc_volatility_{volatility_window}c")

        # Bollinger Band
        bb_slice = closes[max(0, idx - bb_window):idx]
        if len(bb_slice) >= 2:
            bb_mean = bb_slice.mean()
            bb_std = bb_slice.std()
            bw = max((bb_mean + 2 * bb_std) - (bb_mean - 2 * bb_std), 1e-8)
            bb_pos = float(np.clip((closes[idx - 1] - (bb_mean - 2 * bb_std)) / bw, 0.0, 1.0)) - 0.5
        else:
            bb_pos = 0.0
        feats.append(bb_pos)
        names.append(f"f_btc_bb_pos_{bb_window}")

        # Time features
        if use_hour:
            ts_sec = times[idx] / 1000
            hour_frac = (ts_sec % 86400) / 3600
            feats.append(math.sin(2 * math.pi * hour_frac / 24))
            feats.append(math.cos(2 * math.pi * hour_frac / 24))
            names += ["f_decision_hour_utc_sin", "f_decision_hour_utc_cos"]

        if use_weekday:
            ts_sec = times[idx] / 1000
            dow = (int(ts_sec // 86400) + 3) % 7
            feats.append(math.sin(2 * math.pi * dow / 7))
            feats.append(math.cos(2 * math.pi * dow / 7))
            names += ["f_decision_weekday_sin", "f_decision_weekday_cos"]

        # MACD
        if use_macd:
            feats.append(float(np.clip(macd_line[idx - 1], -0.05, 0.05)))
            names.append("f_btc_macd")

        # Stochastic %K
        if use_stochastic:
            lo14 = lows[max(0, idx - 14):idx].min()
            hi14 = highs[max(0, idx - 14):idx].max()
            feats.append(float(np.clip((closes[idx - 1] - lo14) / max(hi14 - lo14, 1e-8), 0.0, 1.0)) - 0.5)
            names.append("f_btc_stochastic_k")

        # ATR ratio
        if use_atr:
            tr_slice = []
            for k in range(max(1, idx - 14), idx):
                tr = max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1]))
                tr_slice.append(tr)
            atr = np.mean(tr_slice) if tr_slice else 1e-8
            feats.append(float(np.clip(atr / max(closes[idx - 1], 1e-8) * 100, 0.0, 5.0)))
            names.append("f_btc_atr_ratio")

        # -- Derivatives features --
        if use_derivatives and derivatives is not None and derivatives.has_data:
            # Funding rate (normalised: typical range -0.01 to +0.01, scale x100)
            fr = derivatives.funding_rate[idx - 1]
            feats.append(float(np.clip(fr * 100 if np.isfinite(fr) else 0.0, -3.0, 3.0)))
            names.append("f_btc_funding_rate")

            # Long/short ratio (centered at 1.0, log-scaled)
            ls = derivatives.ls_ratio[idx - 1]
            feats.append(float(np.clip(math.log(max(ls, 0.01)) if np.isfinite(ls) else 0.0, -2.0, 2.0)))
            names.append("f_btc_ls_ratio_log")

            # Taker buy/sell ratio (centered at 1.0, log-scaled)
            tk = derivatives.taker_ratio[idx - 1]
            feats.append(float(np.clip(math.log(max(tk, 0.01)) if np.isfinite(tk) else 0.0, -2.0, 2.0)))
            names.append("f_btc_taker_ratio_log")

        rows_X.append(feats)
        rows_y.append(label)
        if first_row:
            feat_names = names
            first_row = False

    return np.array(rows_X, dtype=np.float64), np.array(rows_y, dtype=np.int32), feat_names


# ---------------------------------------------------------------------------
# Feature configs
# ---------------------------------------------------------------------------

_BASE_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "baseline_v1",
        "return_lags": [1, 3, 12, 48], "rsi_period": 14, "bb_window": 20,
        "vol_ma_window": 20, "volatility_window": 12, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": False, "use_atr": False,
    },
    {
        "name": "mean_reversion_short",
        "return_lags": [1, 3, 6], "rsi_period": 7, "bb_window": 10,
        "vol_ma_window": 10, "volatility_window": 6, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": True, "use_atr": False,
    },
    {
        "name": "momentum_long",
        "return_lags": [12, 24, 48], "rsi_period": 21, "bb_window": 30,
        "vol_ma_window": 20, "volatility_window": 24, "use_hour": True,
        "use_weekday": True, "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    {
        "name": "kitchen_sink",
        "return_lags": [1, 3, 6, 12, 24, 48], "rsi_period": 14, "bb_window": 20,
        "vol_ma_window": 20, "volatility_window": 12, "use_hour": True,
        "use_weekday": True, "use_macd": True, "use_stochastic": True, "use_atr": True,
    },
    {
        "name": "volume_vol",
        "return_lags": [1, 3, 12], "rsi_period": 14, "bb_window": 20,
        "vol_ma_window": 10, "volatility_window": 6, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": False, "use_atr": True,
    },
    {
        "name": "rsi7_bb10",
        "return_lags": [1, 3], "rsi_period": 7, "bb_window": 10,
        "vol_ma_window": 20, "volatility_window": 12, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": False, "use_atr": False,
    },
    {
        "name": "macd_rsi21",
        "return_lags": [3, 12, 48], "rsi_period": 21, "bb_window": 20,
        "vol_ma_window": 20, "volatility_window": 12, "use_hour": True,
        "use_weekday": False, "use_macd": True, "use_stochastic": False, "use_atr": False,
    },
    {
        "name": "micro_structure",
        "return_lags": [1, 3], "rsi_period": 7, "bb_window": 10,
        "vol_ma_window": 10, "volatility_window": 6, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": True, "use_atr": True,
    },
    {
        "name": "reversal_signals",
        "return_lags": [1, 3, 6], "rsi_period": 7, "bb_window": 14,
        "vol_ma_window": 14, "volatility_window": 8, "use_hour": True,
        "use_weekday": False, "use_macd": False, "use_stochastic": True, "use_atr": True,
    },
]


def _build_feature_configs(has_derivatives: bool) -> list[dict[str, Any]]:
    """Return all feature configs. Duplicate base configs with derivatives=True if data available."""
    configs: list[dict[str, Any]] = []
    for fc in _BASE_CONFIGS:
        configs.append({**fc, "use_derivatives": False})
    if has_derivatives:
        for fc in _BASE_CONFIGS:
            configs.append({**fc, "name": fc["name"] + "+deriv", "use_derivatives": True})
    return configs


# ---------------------------------------------------------------------------
# Hyperparameter grid
# ---------------------------------------------------------------------------

def _build_hp_grid() -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = []

    # Logistic Regression grid
    for l1_ratio, c_val in product(
        [0.0, 0.5, 1.0],
        [0.001, 0.01, 0.1, 1.0, 10.0, 100.0],
    ):
        penalty_name = {0.0: "l2", 0.5: "elasticnet", 1.0: "l1"}[l1_ratio]
        grid.append({
            "model_type": "lr",
            "l1_ratio": l1_ratio,
            "C": c_val,
            "penalty_name": penalty_name,
        })

    # XGBoost grid
    if HAS_XGBOOST:
        for n_est, max_depth, lr, subsample, colsample in product(
            [100, 300],      # n_estimators
            [3, 5, 7],       # max_depth
            [0.01, 0.05],    # learning_rate
            [0.8],           # subsample
            [0.8],           # colsample_bytree
        ):
            grid.append({
                "model_type": "xgb",
                "n_estimators": n_est,
                "max_depth": max_depth,
                "learning_rate": lr,
                "subsample": subsample,
                "colsample_bytree": colsample,
            })

    return grid


def _hp_label(hp: dict[str, Any]) -> str:
    if hp["model_type"] == "lr":
        return f"LR  pen={hp['penalty_name']:>11}  C={hp['C']:>6}"
    return f"XGB n={hp['n_estimators']:>3} d={hp['max_depth']} lr={hp['learning_rate']}"


# ---------------------------------------------------------------------------
# Training + evaluation
# ---------------------------------------------------------------------------

def fit_and_evaluate(
    X: np.ndarray, y: np.ndarray,
    hp: dict[str, Any],
    n_splits: int = 3,
) -> dict[str, float]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    accs, briers, aucs, loglosses = [], [], [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X[train_idx], X[val_idx]
        y_tr, y_va = y[train_idx], y[val_idx]

        if hp["model_type"] == "lr":
            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_va_s = scaler.transform(X_va)
            clf = LogisticRegression(
                l1_ratio=hp["l1_ratio"], C=hp["C"],
                solver="saga", max_iter=500, random_state=42,
            )
            clf.fit(X_tr_s, y_tr)
            proba = clf.predict_proba(X_va_s)[:, 1]
        else:
            clf = XGBClassifier(
                n_estimators=hp["n_estimators"],
                max_depth=hp["max_depth"],
                learning_rate=hp["learning_rate"],
                subsample=hp.get("subsample", 0.8),
                colsample_bytree=hp.get("colsample_bytree", 0.8),
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
                verbosity=0,
            )
            clf.fit(X_tr, y_tr)
            proba = clf.predict_proba(X_va)[:, 1]

        preds = (proba >= 0.5).astype(int)
        accs.append(float((preds == y_va).mean()))
        briers.append(float(brier_score_loss(y_va, proba)))
        aucs.append(float(roc_auc_score(y_va, proba)))
        loglosses.append(float(log_loss(y_va, proba)))

    return {
        "val_acc":     round(float(np.mean(accs)), 6),
        "val_acc_std": round(float(np.std(accs)), 6),
        "val_brier":   round(float(np.mean(briers)), 6),
        "val_auc":     round(float(np.mean(aucs)), 6),
        "val_logloss": round(float(np.mean(loglosses)), 6),
    }


# ---------------------------------------------------------------------------
# Retrain + artifact building
# ---------------------------------------------------------------------------

def retrain_and_build_artifact(
    X: np.ndarray, y: np.ndarray,
    *,
    hp: dict[str, Any],
    fc: dict[str, Any],
    feat_names: list[str],
    cv_metrics: dict[str, float],
    timeframe: str,
) -> tuple[dict[str, Any], Any]:
    """Retrain on full data and build artifact dict. Returns (artifact, fitted_model)."""

    if hp["model_type"] == "lr":
        scaler = StandardScaler()
        X_s = scaler.fit_transform(X)
        clf = LogisticRegression(
            l1_ratio=hp["l1_ratio"], C=hp["C"],
            solver="saga", max_iter=1000, random_state=42,
        )
        clf.fit(X_s, y)
        train_acc = float((clf.predict(X_s) == y).mean())

        artifact = {
            "artifact_type":          "prediction_model_v2",
            "model_name":             f"btc_updown_{timeframe}_lr_v3",
            "model_version":          "v3.0.0",
            "feature_schema_version": f"btc-{timeframe}",
            "description":            f"LR | {fc['name']} | {hp['penalty_name']} C={hp['C']} | val_auc={cv_metrics['val_auc']:.4f}",
            "training_samples":       int(len(y)),
            "yes_rate":               round(float(y.mean()), 6),
            "train_accuracy":         round(train_acc, 6),
            **cv_metrics,
            "feature_columns":        feat_names,
            "required_features":      feat_names[:2],
            "feature_config_name":    fc["name"],
            "feature_config":         {k: v for k, v in fc.items() if k != "name"},
            "hyperparameters":        hp,
            "algorithm_payload": {
                "algorithm":     "logistic_regression",
                "feature_names": feat_names,
                "means":         [round(float(m), 8) for m in scaler.mean_],
                "stds":          [round(float(s), 8) for s in scaler.scale_],
                "weights":       [round(float(w), 8) for w in clf.coef_[0]],
                "bias":          round(float(clf.intercept_[0]), 8),
            },
            "calibration": {"method": "none", "calibration_version": "none", "parameters": {}},
        }

        # Print feature weights
        print(f"\n  Feature weights (top by |weight|):")
        ranked = sorted(zip(feat_names, clf.coef_[0]), key=lambda x: abs(x[1]), reverse=True)
        for fname, w in ranked[:12]:
            print(f"    {fname:<40}  w={w:+.6f}")

        return artifact, clf

    else:  # XGBoost
        clf = XGBClassifier(
            n_estimators=hp["n_estimators"],
            max_depth=hp["max_depth"],
            learning_rate=hp["learning_rate"],
            subsample=hp.get("subsample", 0.8),
            colsample_bytree=hp.get("colsample_bytree", 0.8),
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
            verbosity=0,
        )
        clf.fit(X, y)
        train_acc = float((clf.predict(X) == y).mean())

        xgb_model_filename = f"btc_updown_{timeframe}_xgb_model.json"

        artifact = {
            "artifact_type":          "prediction_model_v2",
            "model_name":             f"btc_updown_{timeframe}_xgb_v3",
            "model_version":          "v3.0.0",
            "feature_schema_version": f"btc-{timeframe}",
            "description":            f"XGB | {fc['name']} | n={hp['n_estimators']} d={hp['max_depth']} lr={hp['learning_rate']} | val_auc={cv_metrics['val_auc']:.4f}",
            "training_samples":       int(len(y)),
            "yes_rate":               round(float(y.mean()), 6),
            "train_accuracy":         round(train_acc, 6),
            **cv_metrics,
            "feature_columns":        feat_names,
            "required_features":      feat_names[:2],
            "feature_config_name":    fc["name"],
            "feature_config":         {k: v for k, v in fc.items() if k != "name"},
            "hyperparameters":        hp,
            "algorithm_payload": {
                "algorithm":      "xgboost",
                "xgb_model_path": xgb_model_filename,
                "feature_names":  feat_names,
            },
            "calibration": {"method": "none", "calibration_version": "none", "parameters": {}},
        }

        # Print feature importances
        print(f"\n  Feature importances (XGBoost gain):")
        importances = clf.feature_importances_
        ranked = sorted(zip(feat_names, importances), key=lambda x: x[1], reverse=True)
        for fname, imp in ranked[:12]:
            print(f"    {fname:<40}  imp={imp:.4f}")

        return artifact, clf


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def run_search(
    candles: list[dict],
    derivatives: DerivativesData | None,
    *,
    timeframe: str,
    n_cv_splits: int = 3,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    """Run full grid search. Returns (best_artifact, all_results, best_fitted_model)."""
    feature_configs = _build_feature_configs(derivatives is not None and derivatives.has_data)
    hp_grid = _build_hp_grid()
    total_configs = len(feature_configs) * len(hp_grid)

    print(f"\n{'='*70}")
    print(f"  Timeframe: {timeframe.upper()}  |  Candles: {len(candles):,}")
    print(f"  Feature configs: {len(feature_configs)}  |  HP combos: {len(hp_grid)}  |  Total: {total_configs}")
    print(f"  Models: LR + {'XGBoost' if HAS_XGBOOST else 'XGBoost (NOT INSTALLED)'}")
    print(f"  Walk-forward folds: {n_cv_splits}")
    print(f"{'='*70}")

    all_results: list[dict[str, Any]] = []
    best_auc = -1.0
    best_info: dict[str, Any] | None = None

    run_idx = 0
    t0_total = time.time()

    for fc in feature_configs:
        t0_feat = time.time()
        try:
            X, y, feat_names = build_features(
                candles, derivatives,
                return_lags=fc["return_lags"], rsi_period=fc["rsi_period"],
                bb_window=fc["bb_window"], vol_ma_window=fc["vol_ma_window"],
                volatility_window=fc["volatility_window"], use_hour=fc["use_hour"],
                use_weekday=fc["use_weekday"], use_macd=fc["use_macd"],
                use_stochastic=fc["use_stochastic"], use_atr=fc["use_atr"],
                use_derivatives=fc.get("use_derivatives", False),
            )
        except Exception as exc:
            print(f"  [SKIP] {fc['name']}: {exc}")
            run_idx += len(hp_grid)
            continue

        if len(X) < 500:
            print(f"  [SKIP] {fc['name']}: only {len(X)} samples")
            run_idx += len(hp_grid)
            continue

        yes_rate = float(y.mean())
        ms = int((time.time() - t0_feat) * 1000)
        print(f"\n  [{fc['name']}]  n={len(X):,}  d={X.shape[1]}  yes={yes_rate:.3f}  {ms}ms")

        for hp in hp_grid:
            run_idx += 1
            t0 = time.time()
            try:
                metrics = fit_and_evaluate(X, y, hp, n_splits=n_cv_splits)
            except Exception as exc:
                print(f"    [{run_idx:>4}/{total_configs}] {_hp_label(hp)} ERROR: {exc}")
                continue

            dt = time.time() - t0
            marker = " <-- BEST" if metrics["val_auc"] > best_auc else ""
            print(
                f"    [{run_idx:>4}/{total_configs}]  {_hp_label(hp)}  "
                f"acc={metrics['val_acc']:.4f}(+/-{metrics['val_acc_std']:.4f})  "
                f"auc={metrics['val_auc']:.4f}  brier={metrics['val_brier']:.4f}  "
                f"{dt:.1f}s{marker}"
            )

            all_results.append({
                "timeframe": timeframe, "feature_config": fc["name"],
                "model_type": hp["model_type"], "n_features": X.shape[1],
                "n_samples": len(X), "yes_rate": yes_rate, **hp, **metrics,
            })

            if metrics["val_auc"] > best_auc:
                best_auc = metrics["val_auc"]
                best_info = {
                    "X": X, "y": y, "feat_names": feat_names,
                    "fc": fc, "hp": hp, "metrics": metrics,
                }

    elapsed_total = time.time() - t0_total
    print(f"\n  Search complete in {elapsed_total / 60:.1f} min")

    if best_info is None:
        raise RuntimeError("No valid results")

    # Retrain best
    print(f"\n  Retraining best: {best_info['fc']['name']}  {_hp_label(best_info['hp'])}")
    artifact, fitted_model = retrain_and_build_artifact(
        best_info["X"], best_info["y"],
        hp=best_info["hp"], fc=best_info["fc"],
        feat_names=best_info["feat_names"],
        cv_metrics=best_info["metrics"],
        timeframe=timeframe,
    )
    print(f"  train_acc={artifact['train_accuracy']:.4f}  val_auc(cv)={best_auc:.4f}")
    return artifact, all_results, fitted_model


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="BTC model grid search (XGBoost + LR)")
    parser.add_argument("--interval", default="all",
                        help=f"Options: {', '.join(SUPPORTED_INTERVALS)}, all (default: all = 5m+15m)")
    parser.add_argument("--cv-splits", type=int, default=3, help="Walk-forward CV folds (default: 3)")
    args = parser.parse_args()

    interval_arg = args.interval.strip().lower()
    if interval_arg == "all":
        intervals = ["5m", "15m"]
    elif interval_arg in _INTERVAL_FACTOR:
        intervals = [interval_arg]
    else:
        print(f"ERROR: unknown interval '{interval_arg}'. Options: {', '.join(SUPPORTED_INTERVALS)}, all")
        return

    if not BASE_OHLCV_PATH.exists():
        print(f"ERROR: {BASE_OHLCV_PATH} not found. Run: python scripts/btc_fetch_ohlcv.py")
        return

    print(f"Loading 5m candles from {BASE_OHLCV_PATH} ...")
    candles_5m: list[dict] = []
    with BASE_OHLCV_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s:
                candles_5m.append(json.loads(s))
    candles_5m.sort(key=lambda c: c["open_time_ms"])
    print(f"  {len(candles_5m):,} 5m candles loaded")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_artifacts: list[tuple[str, dict[str, Any]]] = []

    for interval in intervals:
        candles = load_or_resample(interval, candles_5m)

        # Load derivatives data aligned to this candle set
        times_ms = np.array([c["open_time_ms"] for c in candles])
        deriv = DerivativesData(times_ms)

        artifact, results, fitted_model = run_search(
            candles, deriv, timeframe=interval, n_cv_splits=args.cv_splits,
        )

        # Save artifact
        art_path = OUT_DIR / f"btc_updown_{interval}_best.json"
        art_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(f"\n  [{interval}] Artifact: {art_path}")

        # Save XGBoost native model if applicable
        algo = artifact.get("algorithm_payload", {}).get("algorithm", "")
        if algo == "xgboost" and hasattr(fitted_model, "save_model"):
            xgb_path = OUT_DIR / artifact["algorithm_payload"]["xgb_model_path"]
            fitted_model.save_model(str(xgb_path))
            print(f"  [{interval}] XGB model: {xgb_path}")

        # Save grid results
        res_path = OUT_DIR / f"grid_search_results_{interval}.json"
        results.sort(key=lambda r: r.get("val_auc", 0), reverse=True)
        res_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  [{interval}] Grid results: {res_path}  ({len(results)} entries)")

        all_artifacts.append((interval, artifact))

    # Summary
    print("\n" + "=" * 70)
    print("  FINAL SUMMARY")
    print("=" * 70)
    for interval, art in all_artifacts:
        algo = art.get("algorithm_payload", {}).get("algorithm", "?")
        config_name = art.get("feature_config_name", "?")
        print(
            f"  [{interval:>3}]  {algo:<5}  config={config_name:<25}"
            f"  d={len(art['feature_columns'])}  "
            f"val_acc={art['val_accuracy']:.4f}(+/-{art['val_accuracy_std']:.4f})  "
            f"val_auc={art['val_auc']:.4f}  brier={art['val_brier']:.4f}"
        )

    print("\nNext steps:")
    for interval, art in all_artifacts:
        path = OUT_DIR / f"btc_updown_{interval}_best.json"
        print(f"  [{interval}] model_artifact_path: {path}")
        print(f"  [{interval}] feature_schema_version: {art['feature_schema_version']}")


if __name__ == "__main__":
    main()
