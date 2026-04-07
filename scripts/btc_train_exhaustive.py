"""Exhaustive BTC Up/Down model search — LR + LightGBM + XGBoost + CatBoost + Stacking.

Grid search over:
  - 5 model types: Logistic Regression, LightGBM, XGBoost, CatBoost, Stacking ensemble
  - 18 feature configs (OHLCV technicals + derivatives: funding rate, LS/taker ratio,
    order book imbalance, open interest)
  - Multiple hyperparameter combos per model type
  - 3-fold walk-forward cross-validation (no future leakage)

Metrics: AUC-ROC (primary), accuracy, Brier score, log-loss

Usage:
    # Fetch data first:
    python scripts/btc_fetch_ohlcv.py --months 24
    python scripts/btc_fetch_derivatives.py --months 24

    # Train on single interval:
    python scripts/btc_train_exhaustive.py --interval 5m
    python scripts/btc_train_exhaustive.py --interval 15m

    # Train all (default = 5m + 15m):
    python scripts/btc_train_exhaustive.py

Outputs (per interval):
    data/models/v3/btc_updown_{interval}_best.json
    data/models/v3/btc_updown_{interval}_{model}.model  (LightGBM/XGB/CatBoost native)
    data/models/v3/grid_search_results_{interval}.json
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

warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
warnings.filterwarnings("ignore", category=UserWarning)

# Optional heavy deps — graceful fallback
try:
    from xgboost import XGBClassifier
    HAS_XGBOOST = True
except ImportError:
    HAS_XGBOOST = False

try:
    import lightgbm as lgb
    HAS_LIGHTGBM = True
except ImportError:
    HAS_LIGHTGBM = False

try:
    from catboost import CatBoostClassifier
    HAS_CATBOOST = True
except ImportError:
    HAS_CATBOOST = False

print(f"  Models available: LR=True  LightGBM={HAS_LIGHTGBM}  XGBoost={HAS_XGBOOST}  CatBoost={HAS_CATBOOST}")

# ---------------------------------------------------------------------------
# Paths and interval config
# ---------------------------------------------------------------------------
BASE_OHLCV_PATH = Path("data/btc/ohlcv_5m.jsonl")
FUNDING_PATH    = Path("data/btc/funding_rate.jsonl")
LS_RATIO_PATH   = Path("data/btc/long_short_ratio.jsonl")
TAKER_PATH      = Path("data/btc/taker_ratio.jsonl")
OI_PATH         = Path("data/btc/open_interest.jsonl")
OB_PATH         = Path("data/btc/order_book_snapshots.jsonl")
OUT_DIR         = Path("data/models/v3")

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
        out: list[dict] = []
        with native_path.open(encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s:
                    out.append(json.loads(s))
        out.sort(key=lambda c: c["open_time_ms"])
        return out
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
            s = line.strip()
            if s:
                rows.append(json.loads(s))
    rows.sort(key=lambda r: r["timestamp_ms"])
    return rows


def _align_series(candle_times_ms: np.ndarray, series: list[dict], value_key: str) -> np.ndarray:
    """For each candle, look up the most recent value from `series` (by timestamp)."""
    n = len(candle_times_ms)
    result = np.full(n, np.nan)
    if not series:
        return result
    ts   = [r["timestamp_ms"] for r in series]
    vals = [r[value_key] for r in series]
    for i in range(n):
        idx = bisect_right(ts, int(candle_times_ms[i])) - 1
        if idx >= 0:
            result[i] = vals[idx]
    return result


class DerivativesData:
    """Pre-aligned derivatives arrays indexed by candle position."""

    def __init__(self, candle_times_ms: np.ndarray) -> None:
        self.has_any = False

        funding_raw = _load_jsonl(FUNDING_PATH)
        ls_raw      = _load_jsonl(LS_RATIO_PATH)
        taker_raw   = _load_jsonl(TAKER_PATH)
        oi_raw      = _load_jsonl(OI_PATH)
        ob_raw      = _load_jsonl(OB_PATH)

        self.funding_rate  = _align_series(candle_times_ms, funding_raw, "funding_rate")
        self.ls_ratio      = _align_series(candle_times_ms, ls_raw,      "long_short_ratio")
        self.taker_ratio   = _align_series(candle_times_ms, taker_raw,   "buy_sell_ratio")
        self.oi            = _align_series(candle_times_ms, oi_raw,      "open_interest")
        self.ob_imbalance  = _align_series(candle_times_ms, ob_raw,      "imbalance")

        counts = {
            "funding":     int(np.isfinite(self.funding_rate).sum()),
            "ls_ratio":    int(np.isfinite(self.ls_ratio).sum()),
            "taker":       int(np.isfinite(self.taker_ratio).sum()),
            "oi":          int(np.isfinite(self.oi).sum()),
            "ob_imbalance":int(np.isfinite(self.ob_imbalance).sum()),
        }
        total = len(candle_times_ms)
        self.has_any = any(v > 0 for v in counts.values())
        available = [k for k, v in counts.items() if v > 0]
        print(f"  [derivatives] total={total}  available={available}")
        for k, v in counts.items():
            if v > 0:
                print(f"    {k}: {v}/{total} ({v/total*100:.0f}%)")


# ---------------------------------------------------------------------------
# Feature computation
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
    n = len(candles)
    opens  = np.array([c["open"]        for c in candles])
    highs  = np.array([c["high"]        for c in candles])
    lows   = np.array([c["low"]         for c in candles])
    closes = np.array([c["close"]       for c in candles])
    vols   = np.array([c["volume"]      for c in candles])
    times  = np.array([c["open_time_ms"] for c in candles])

    rsi_arr  = _rsi_series(closes, rsi_period)
    macd_arr = None
    if use_macd:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_arr = (ema12 - ema26) / np.maximum(closes, 1e-8)

    required_warmup = max(
        max(return_lags, default=1), rsi_period + 1, bb_window,
        vol_ma_window, volatility_window,
        26 if use_macd else 1,
        14 if use_stochastic else 1,
    ) + 2

    rows_X: list[list[float]] = []
    rows_y: list[int] = []
    feat_names: list[str] = []
    first_row = True

    for idx in range(required_warmup, n - 1):
        label = 1 if candles[idx]["close"] > candles[idx]["open"] else 0
        feats: list[float] = []
        names: list[str] = []

        # Returns
        prev_close = closes[idx - 1]
        for lag in return_lags:
            base = closes[max(0, idx - lag)]
            feats.append(float(np.clip((prev_close - base) / max(base, 1e-8), -0.30, 0.30)))
            names.append(f"f_btc_return_{lag}c")

        # RSI
        feats.append(float(np.clip((rsi_arr[idx - 1] - 0.5) * 2.0, -1.0, 1.0)))
        names.append(f"f_btc_rsi_{rsi_period}")

        # Volume ratio
        vol_sl = vols[max(0, idx - vol_ma_window):idx]
        avg_vol = vol_sl.mean() if len(vol_sl) > 0 else vols[idx - 1]
        feats.append(float(np.clip(math.log(max(vols[idx - 1], 1e-8) / max(avg_vol, 1e-8)), -3.0, 3.0)))
        names.append(f"f_btc_vol_ratio_ma{vol_ma_window}")

        # Realized volatility
        cl_sl = closes[max(0, idx - volatility_window):idx]
        if len(cl_sl) >= 2:
            rets = np.diff(cl_sl) / np.maximum(cl_sl[:-1], 1e-8)
            feats.append(float(np.clip(rets.std() * 100, 0.0, 5.0)))
        else:
            feats.append(0.0)
        names.append(f"f_btc_volatility_{volatility_window}c")

        # Bollinger Band
        bb_sl = closes[max(0, idx - bb_window):idx]
        if len(bb_sl) >= 2:
            bb_mean = bb_sl.mean()
            bb_std  = bb_sl.std()
            bw = max((bb_mean + 2 * bb_std) - (bb_mean - 2 * bb_std), 1e-8)
            feats.append(float(np.clip((closes[idx - 1] - (bb_mean - 2 * bb_std)) / bw, 0.0, 1.0)) - 0.5)
        else:
            feats.append(0.0)
        names.append(f"f_btc_bb_pos_{bb_window}")

        # Time
        if use_hour:
            ts_sec = times[idx] / 1000
            hf = (ts_sec % 86400) / 3600
            feats += [math.sin(2 * math.pi * hf / 24), math.cos(2 * math.pi * hf / 24)]
            names += ["f_decision_hour_utc_sin", "f_decision_hour_utc_cos"]

        if use_weekday:
            ts_sec = times[idx] / 1000
            dow = (int(ts_sec // 86400) + 3) % 7
            feats += [math.sin(2 * math.pi * dow / 7), math.cos(2 * math.pi * dow / 7)]
            names += ["f_decision_weekday_sin", "f_decision_weekday_cos"]

        # MACD
        if use_macd:
            feats.append(float(np.clip(macd_arr[idx - 1], -0.05, 0.05)))
            names.append("f_btc_macd")

        # Stochastic %K
        if use_stochastic:
            lo14 = lows[max(0, idx - 14):idx].min()
            hi14 = highs[max(0, idx - 14):idx].max()
            feats.append(float(np.clip((closes[idx - 1] - lo14) / max(hi14 - lo14, 1e-8), 0.0, 1.0)) - 0.5)
            names.append("f_btc_stochastic_k")

        # ATR
        if use_atr:
            tr_s = [max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1]))
                    for k in range(max(1, idx - 14), idx)]
            atr = np.mean(tr_s) if tr_s else 1e-8
            feats.append(float(np.clip(atr / max(closes[idx - 1], 1e-8) * 100, 0.0, 5.0)))
            names.append("f_btc_atr_ratio")

        # Derivatives
        if use_derivatives and derivatives is not None and derivatives.has_any:
            i1 = idx - 1

            fr = derivatives.funding_rate[i1]
            feats.append(float(np.clip(fr * 100 if np.isfinite(fr) else 0.0, -3.0, 3.0)))
            names.append("f_btc_funding_rate")

            ls = derivatives.ls_ratio[i1]
            feats.append(float(np.clip(math.log(max(ls, 0.01)) if np.isfinite(ls) and ls > 0 else 0.0, -2.0, 2.0)))
            names.append("f_btc_ls_ratio_log")

            tk = derivatives.taker_ratio[i1]
            feats.append(float(np.clip(math.log(max(tk, 0.01)) if np.isfinite(tk) and tk > 0 else 0.0, -2.0, 2.0)))
            names.append("f_btc_taker_ratio_log")

            # Open interest — percentage change vs previous reading
            oi_cur  = derivatives.oi[i1]
            oi_prev = derivatives.oi[max(0, i1 - 1)]
            if np.isfinite(oi_cur) and np.isfinite(oi_prev) and oi_prev > 0:
                oi_chg = (oi_cur - oi_prev) / oi_prev
                feats.append(float(np.clip(oi_chg * 100, -5.0, 5.0)))
            else:
                feats.append(0.0)
            names.append("f_btc_oi_change_pct")

            # Order book imbalance (already in [-1, 1] range)
            ob = derivatives.ob_imbalance[i1]
            feats.append(float(np.clip(ob if np.isfinite(ob) else 0.0, -1.0, 1.0)))
            names.append("f_btc_ob_imbalance")

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
    configs = [{**fc, "use_derivatives": False} for fc in _BASE_CONFIGS]
    if has_derivatives:
        configs += [{**fc, "name": fc["name"] + "+deriv", "use_derivatives": True} for fc in _BASE_CONFIGS]
    return configs


# ---------------------------------------------------------------------------
# HP grid — one entry per model type x hyperparameter combo
# ---------------------------------------------------------------------------

def _build_hp_grid() -> list[dict[str, Any]]:
    grid: list[dict[str, Any]] = []

    # Logistic Regression
    for l1_ratio, c_val in product([0.0, 0.5, 1.0], [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]):
        pname = {0.0: "l2", 0.5: "elasticnet", 1.0: "l1"}[l1_ratio]
        grid.append({"model_type": "lr", "l1_ratio": l1_ratio, "C": c_val, "penalty_name": pname})

    # LightGBM
    if HAS_LIGHTGBM:
        for n_est, num_leaves, lr, min_data in product(
            [200, 500],       # n_estimators
            [15, 31, 63],     # num_leaves
            [0.01, 0.05],     # learning_rate
            [20],             # min_child_samples
        ):
            grid.append({"model_type": "lgbm", "n_estimators": n_est,
                          "num_leaves": num_leaves, "learning_rate": lr, "min_child_samples": min_data})

    # XGBoost
    if HAS_XGBOOST:
        for n_est, max_depth, lr, subsample in product(
            [200, 500], [3, 5, 7], [0.01, 0.05], [0.8],
        ):
            grid.append({"model_type": "xgb", "n_estimators": n_est,
                          "max_depth": max_depth, "learning_rate": lr, "subsample": subsample})

    # CatBoost
    if HAS_CATBOOST:
        for n_est, depth, lr in product([300, 600], [4, 6], [0.01, 0.05]):
            grid.append({"model_type": "cat", "n_estimators": n_est, "depth": depth, "learning_rate": lr})

    # Stacking (always present — uses LR as meta, best base models as level-0)
    grid.append({"model_type": "stack", "meta": "lr", "base_models": "lgbm+xgb+cat"})

    return grid


def _hp_label(hp: dict[str, Any]) -> str:
    mt = hp["model_type"]
    if mt == "lr":
        return f"LR    pen={hp['penalty_name']:>11}  C={hp['C']:>6}"
    if mt == "lgbm":
        return f"LGBM  n={hp['n_estimators']:>3}  leaves={hp['num_leaves']:>2}  lr={hp['learning_rate']}"
    if mt == "xgb":
        return f"XGB   n={hp['n_estimators']:>3}  d={hp['max_depth']}  lr={hp['learning_rate']}"
    if mt == "cat":
        return f"CAT   n={hp['n_estimators']:>3}  d={hp['depth']}  lr={hp['learning_rate']}"
    return f"STACK base={hp.get('base_models','?')}"


# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------

def _make_lgbm(hp: dict[str, Any]) -> Any:
    return lgb.LGBMClassifier(
        n_estimators=hp["n_estimators"],
        num_leaves=hp["num_leaves"],
        learning_rate=hp["learning_rate"],
        min_child_samples=hp["min_child_samples"],
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )


def _make_xgb(hp: dict[str, Any]) -> Any:
    return XGBClassifier(
        n_estimators=hp["n_estimators"],
        max_depth=hp["max_depth"],
        learning_rate=hp["learning_rate"],
        subsample=hp.get("subsample", 0.8),
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )


def _make_cat(hp: dict[str, Any]) -> Any:
    return CatBoostClassifier(
        iterations=hp["n_estimators"],
        depth=hp["depth"],
        learning_rate=hp["learning_rate"],
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
    )


# ---------------------------------------------------------------------------
# Training + evaluation
# ---------------------------------------------------------------------------

def _fit_base(clf: Any, X_tr: np.ndarray, y_tr: np.ndarray, model_type: str) -> None:
    if model_type == "lgbm":
        clf.fit(X_tr, y_tr, callbacks=[lgb.log_evaluation(period=-1)])
    else:
        clf.fit(X_tr, y_tr)


def fit_and_evaluate(
    X: np.ndarray,
    y: np.ndarray,
    hp: dict[str, Any],
    n_splits: int = 3,
) -> dict[str, float]:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    accs, briers, aucs, lls = [], [], [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X[train_idx], X[val_idx]
        y_tr, y_va = y[train_idx], y[val_idx]

        mt = hp["model_type"]

        if mt == "lr":
            sc = StandardScaler()
            X_tr_s = sc.fit_transform(X_tr)
            X_va_s = sc.transform(X_va)
            clf = LogisticRegression(l1_ratio=hp["l1_ratio"], C=hp["C"],
                                     solver="saga", max_iter=500, random_state=42)
            clf.fit(X_tr_s, y_tr)
            proba = clf.predict_proba(X_va_s)[:, 1]

        elif mt == "lgbm":
            clf = _make_lgbm(hp)
            _fit_base(clf, X_tr, y_tr, "lgbm")
            proba = clf.predict_proba(X_va)[:, 1]

        elif mt == "xgb":
            clf = _make_xgb(hp)
            clf.fit(X_tr, y_tr)
            proba = clf.predict_proba(X_va)[:, 1]

        elif mt == "cat":
            clf = _make_cat(hp)
            clf.fit(X_tr, y_tr)
            proba = clf.predict_proba(X_va)[:, 1]

        elif mt == "stack":
            proba = _stack_predict(X_tr, y_tr, X_va)

        else:
            continue

        preds = (proba >= 0.5).astype(int)
        accs.append(float((preds == y_va).mean()))
        briers.append(float(brier_score_loss(y_va, proba)))
        aucs.append(float(roc_auc_score(y_va, proba)))
        lls.append(float(log_loss(y_va, proba)))

    return {
        "val_acc":     round(float(np.mean(accs)), 6),
        "val_acc_std": round(float(np.std(accs)), 6),
        "val_brier":   round(float(np.mean(briers)), 6),
        "val_auc":     round(float(np.mean(aucs)), 6),
        "val_logloss": round(float(np.mean(lls)), 6),
    }


def _stack_predict(X_tr: np.ndarray, y_tr: np.ndarray, X_va: np.ndarray) -> np.ndarray:
    """Level-0: LightGBM + XGBoost + CatBoost. Level-1: Logistic Regression."""
    base_probas_tr: list[np.ndarray] = []
    base_probas_va: list[np.ndarray] = []

    # Use inner CV to generate out-of-fold predictions for level-1 training
    inner_cv = TimeSeriesSplit(n_splits=2)

    for make_fn, available, name in [
        (_make_lgbm, HAS_LIGHTGBM, "lgbm"),
        (_make_xgb,  HAS_XGBOOST,  "xgb"),
        (_make_cat,  HAS_CATBOOST,  "cat"),
    ]:
        if not available:
            continue
        hp_base = {
            "lgbm": {"n_estimators": 200, "num_leaves": 31, "learning_rate": 0.05, "min_child_samples": 20},
            "xgb":  {"n_estimators": 200, "max_depth": 5,   "learning_rate": 0.05, "subsample": 0.8},
            "cat":  {"n_estimators": 300, "depth": 5,        "learning_rate": 0.05},
        }[name]

        # OOF for meta-training
        oof = np.zeros(len(X_tr))
        for tr_i, va_i in inner_cv.split(X_tr):
            clf = make_fn(hp_base)
            _fit_base(clf, X_tr[tr_i], y_tr[tr_i], name)
            oof[va_i] = clf.predict_proba(X_tr[va_i])[:, 1]
        base_probas_tr.append(oof)

        # Full fit for validation prediction
        clf = make_fn(hp_base)
        _fit_base(clf, X_tr, y_tr, name)
        base_probas_va.append(clf.predict_proba(X_va)[:, 1])

    if not base_probas_tr:
        # Fallback: plain LR
        sc = StandardScaler()
        lr = LogisticRegression(C=1.0, solver="lbfgs", max_iter=300, random_state=42)
        lr.fit(sc.fit_transform(X_tr), y_tr)
        return lr.predict_proba(sc.transform(X_va))[:, 1]

    # Stack meta-features
    meta_tr = np.column_stack(base_probas_tr)
    meta_va = np.column_stack(base_probas_va)

    sc = StandardScaler()
    meta_clf = LogisticRegression(C=1.0, solver="lbfgs", max_iter=300, random_state=42)
    meta_clf.fit(sc.fit_transform(meta_tr), y_tr)
    return meta_clf.predict_proba(sc.transform(meta_va))[:, 1]


# ---------------------------------------------------------------------------
# Full retrain + artifact building
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
    mt = hp["model_type"]

    if mt == "lr":
        sc = StandardScaler()
        X_s = sc.fit_transform(X)
        clf = LogisticRegression(l1_ratio=hp["l1_ratio"], C=hp["C"],
                                 solver="saga", max_iter=1000, random_state=42)
        clf.fit(X_s, y)
        train_acc = float((clf.predict(X_s) == y).mean())
        print(f"\n  Feature weights (top by |weight|):")
        ranked = sorted(zip(feat_names, clf.coef_[0]), key=lambda x: abs(x[1]), reverse=True)
        for fname, w in ranked[:12]:
            print(f"    {fname:<40}  w={w:+.6f}")
        artifact = _base_artifact(timeframe, fc, hp, cv_metrics, feat_names, len(y), float(y.mean()), train_acc)
        artifact["algorithm_payload"] = {
            "algorithm": "logistic_regression", "feature_names": feat_names,
            "means":   [round(float(m), 8) for m in sc.mean_],
            "stds":    [round(float(s), 8) for s in sc.scale_],
            "weights": [round(float(w), 8) for w in clf.coef_[0]],
            "bias":    round(float(clf.intercept_[0]), 8),
        }
        return artifact, clf

    model_file = f"btc_updown_{timeframe}_{mt}.model"

    if mt == "lgbm":
        clf = _make_lgbm(hp)
        _fit_base(clf, X, y, "lgbm")
        train_acc = float((clf.predict(X) == y).mean())
        _print_importances(feat_names, clf.feature_importances_, "gain")
        artifact = _base_artifact(timeframe, fc, hp, cv_metrics, feat_names, len(y), float(y.mean()), train_acc)
        artifact["algorithm_payload"] = {
            "algorithm": "lightgbm", "lgbm_model_path": model_file, "feature_names": feat_names,
        }
        return artifact, clf

    if mt == "xgb":
        clf = _make_xgb(hp)
        clf.fit(X, y)
        train_acc = float((clf.predict(X) == y).mean())
        _print_importances(feat_names, clf.feature_importances_, "gain")
        artifact = _base_artifact(timeframe, fc, hp, cv_metrics, feat_names, len(y), float(y.mean()), train_acc)
        artifact["algorithm_payload"] = {
            "algorithm": "xgboost", "xgb_model_path": model_file, "feature_names": feat_names,
        }
        return artifact, clf

    if mt == "cat":
        clf = _make_cat(hp)
        clf.fit(X, y)
        train_acc = float((clf.predict(X) == y).mean())
        importances = clf.get_feature_importance()
        _print_importances(feat_names, importances, "importance")
        artifact = _base_artifact(timeframe, fc, hp, cv_metrics, feat_names, len(y), float(y.mean()), train_acc)
        artifact["algorithm_payload"] = {
            "algorithm": "catboost", "cat_model_path": model_file, "feature_names": feat_names,
        }
        return artifact, clf

    if mt == "stack":
        # For stacking: retrain all base models on full data, save each
        base_clfs: dict[str, Any] = {}
        base_paths: dict[str, str] = {}
        for name, available, make_fn in [
            ("lgbm", HAS_LIGHTGBM, _make_lgbm),
            ("xgb",  HAS_XGBOOST,  _make_xgb),
            ("cat",  HAS_CATBOOST,  _make_cat),
        ]:
            if not available:
                continue
            hp_base = {
                "lgbm": {"n_estimators": 200, "num_leaves": 31, "learning_rate": 0.05, "min_child_samples": 20},
                "xgb":  {"n_estimators": 200, "max_depth": 5,   "learning_rate": 0.05, "subsample": 0.8},
                "cat":  {"n_estimators": 300, "depth": 5,        "learning_rate": 0.05},
            }[name]
            c = make_fn(hp_base)
            _fit_base(c, X, y, name)
            base_clfs[name] = c
            base_paths[name] = f"btc_updown_{timeframe}_stack_{name}.model"

        # Train meta
        meta_tr = np.column_stack([c.predict_proba(X)[:, 1] for c in base_clfs.values()])
        sc = StandardScaler()
        meta = LogisticRegression(C=1.0, solver="lbfgs", max_iter=300, random_state=42)
        meta.fit(sc.fit_transform(meta_tr), y)
        meta_proba = meta.predict_proba(sc.transform(meta_tr))[:, 1]
        train_acc = float(((meta_proba >= 0.5).astype(int) == y).mean())

        artifact = _base_artifact(timeframe, fc, hp, cv_metrics, feat_names, len(y), float(y.mean()), train_acc)
        artifact["algorithm_payload"] = {
            "algorithm": "stacking",
            "base_model_paths": base_paths,
            "meta_weights": [round(float(w), 8) for w in meta.coef_[0]],
            "meta_bias": round(float(meta.intercept_[0]), 8),
            "meta_scaler_means": [round(float(m), 8) for m in sc.mean_],
            "meta_scaler_stds":  [round(float(s), 8) for s in sc.scale_],
            "feature_names": feat_names,
        }
        return artifact, (base_clfs, meta, sc)

    raise ValueError(f"Unknown model_type={mt}")


def _base_artifact(
    timeframe: str, fc: dict, hp: dict, cv_metrics: dict,
    feat_names: list, n_samples: int, yes_rate: float, train_acc: float,
) -> dict[str, Any]:
    mt = hp["model_type"]
    return {
        "artifact_type":          "prediction_model_v2",
        "model_name":             f"btc_updown_{timeframe}_{mt}_v3",
        "model_version":          "v3.0.0",
        "feature_schema_version": f"btc-{timeframe}",
        "description":            f"{mt.upper()} | {fc['name']} | {_hp_label(hp)} | val_auc={cv_metrics['val_auc']:.4f}",
        "training_samples":       n_samples,
        "yes_rate":               round(yes_rate, 6),
        "train_accuracy":         round(train_acc, 6),
        **cv_metrics,
        "feature_columns":        feat_names,
        "required_features":      feat_names[:2],
        "feature_config_name":    fc["name"],
        "feature_config":         {k: v for k, v in fc.items() if k != "name"},
        "hyperparameters":        hp,
        "algorithm_payload":      {},  # filled by caller
        "calibration": {"method": "none", "calibration_version": "none", "parameters": {}},
    }


def _print_importances(feat_names: list[str], importances: Any, label: str) -> None:
    print(f"\n  Feature importances ({label}, top 12):")
    ranked = sorted(zip(feat_names, importances), key=lambda x: x[1], reverse=True)
    for fname, imp in ranked[:12]:
        print(f"    {fname:<40}  {label}={imp:.4f}")


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
    feature_configs = _build_feature_configs(derivatives is not None and derivatives.has_any)
    hp_grid = _build_hp_grid()
    total = len(feature_configs) * len(hp_grid)

    model_counts = {}
    for hp in hp_grid:
        model_counts[hp["model_type"]] = model_counts.get(hp["model_type"], 0) + 1

    print(f"\n{'='*70}")
    print(f"  Timeframe: {timeframe.upper()}  |  Candles: {len(candles):,}")
    print(f"  Feature configs: {len(feature_configs)}  |  HP combos: {len(hp_grid)}  |  Total: {total}")
    print(f"  Per model: {model_counts}")
    print(f"{'='*70}")

    all_results: list[dict[str, Any]] = []
    best_auc = -1.0
    best_info: dict[str, Any] | None = None
    run_idx = 0
    t0_total = time.time()

    for fc in feature_configs:
        t0 = time.time()
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

        ms = int((time.time() - t0) * 1000)
        print(f"\n  [{fc['name']}]  n={len(X):,}  d={X.shape[1]}  yes={y.mean():.3f}  {ms}ms")

        for hp in hp_grid:
            run_idx += 1
            t0_hp = time.time()
            try:
                metrics = fit_and_evaluate(X, y, hp, n_splits=n_cv_splits)
            except Exception as exc:
                print(f"    [{run_idx:>4}/{total}] {_hp_label(hp)} ERROR: {exc}")
                continue

            dt = time.time() - t0_hp
            marker = " <-- BEST" if metrics["val_auc"] > best_auc else ""
            print(
                f"    [{run_idx:>4}/{total}]  {_hp_label(hp):<45}  "
                f"acc={metrics['val_acc']:.4f}  auc={metrics['val_auc']:.4f}  "
                f"brier={metrics['val_brier']:.4f}  {dt:.1f}s{marker}"
            )

            all_results.append({
                "timeframe": timeframe, "feature_config": fc["name"],
                "model_type": hp["model_type"], "n_features": X.shape[1],
                "n_samples": len(X), "yes_rate": float(y.mean()),
                **{k: v for k, v in hp.items() if k != "model_type"},
                **metrics,
            })

            if metrics["val_auc"] > best_auc:
                best_auc = metrics["val_auc"]
                best_info = {"X": X, "y": y, "feat_names": feat_names, "fc": fc, "hp": hp, "metrics": metrics}

    elapsed = time.time() - t0_total
    print(f"\n  Search complete in {elapsed / 60:.1f} min")

    if best_info is None:
        raise RuntimeError("No valid results")

    print(f"\n  Retraining best: [{best_info['fc']['name']}]  {_hp_label(best_info['hp'])}")
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
# Save helpers
# ---------------------------------------------------------------------------

def save_model_file(fitted_model: Any, artifact: dict[str, Any], out_dir: Path) -> None:
    algo = artifact.get("algorithm_payload", {}).get("algorithm", "")
    if algo == "logistic_regression":
        return  # weights embedded in artifact

    if algo == "lightgbm":
        path = out_dir / artifact["algorithm_payload"]["lgbm_model_path"]
        fitted_model.booster_.save_model(str(path))
        print(f"    LGBM model: {path}")

    elif algo == "xgboost":
        path = out_dir / artifact["algorithm_payload"]["xgb_model_path"]
        fitted_model.save_model(str(path))
        print(f"    XGB model: {path}")

    elif algo == "catboost":
        path = out_dir / artifact["algorithm_payload"]["cat_model_path"]
        fitted_model.save_model(str(path))
        print(f"    CAT model: {path}")

    elif algo == "stacking":
        base_clfs, meta, sc = fitted_model
        for name, clf in base_clfs.items():
            rel_path = artifact["algorithm_payload"]["base_model_paths"][name]
            path = out_dir / rel_path
            if name == "lgbm":
                clf.booster_.save_model(str(path))
            elif name == "xgb":
                clf.save_model(str(path))
            elif name == "cat":
                clf.save_model(str(path))
            print(f"    Stack [{name}] model: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="BTC model grid search: LR + LightGBM + XGBoost + CatBoost + Stacking")
    parser.add_argument("--interval", default="all",
                        help=f"Options: {', '.join(SUPPORTED_INTERVALS)}, all (default: 5m+15m)")
    parser.add_argument("--cv-splits", type=int, default=3, help="Walk-forward CV folds (default: 3)")
    args = parser.parse_args()

    interval_arg = args.interval.strip().lower()
    if interval_arg == "all":
        intervals = ["5m", "15m"]
    elif interval_arg in _INTERVAL_FACTOR:
        intervals = [interval_arg]
    else:
        print(f"ERROR: unknown interval '{interval_arg}'")
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
        times_ms = np.array([c["open_time_ms"] for c in candles])
        deriv = DerivativesData(times_ms)

        artifact, results, fitted_model = run_search(
            candles, deriv, timeframe=interval, n_cv_splits=args.cv_splits,
        )

        art_path = OUT_DIR / f"btc_updown_{interval}_best.json"
        art_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(f"\n  [{interval}] Artifact: {art_path}")

        save_model_file(fitted_model, artifact, OUT_DIR)

        res_path = OUT_DIR / f"grid_search_results_{interval}.json"
        results.sort(key=lambda r: r.get("val_auc", 0), reverse=True)
        res_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"  [{interval}] Grid results: {res_path}  ({len(results)} entries)")

        all_artifacts.append((interval, artifact))

    print("\n" + "=" * 70)
    print("  FINAL SUMMARY")
    print("=" * 70)
    for interval, art in all_artifacts:
        algo = art.get("algorithm_payload", {}).get("algorithm", "?")
        config_name = art.get("feature_config_name", "?")
        print(
            f"  [{interval:>3}]  {algo:<14}  config={config_name:<25}"
            f"  d={len(art['feature_columns'])}  "
            f"val_auc={art['val_auc']:.4f}  brier={art['val_brier']:.4f}"
        )

    print("\nNext steps:")
    for interval, art in all_artifacts:
        print(f"  [{interval}] model_artifact_path: {OUT_DIR / f'btc_updown_{interval}_best.json'}")
        print(f"  [{interval}] feature_schema_version: {art['feature_schema_version']}")


if __name__ == "__main__":
    main()
