"""Train v7 — directly learns the TP-vs-SL classifier for HL perp.

Problem with v5/v6: they predict "BTC up/down at next bar close".  That's
not what we trade.  We trade: "open LONG now, will TP_long fire before
SL_long inside the next N bars?"  Two events have ~70% correlation, but
the 30% disagreement is exactly the trades that destroy realistic PnL
(direction right, magnitude wrong).

v7 trains two binary classifiers per bar:
   y_long  = 1 if HIGH crosses entry × (1+tp_pct) BEFORE LOW crosses
                  entry × (1-sl_pct) within the next horizon_bars
   y_short = mirror

At runtime the executor picks the side with the higher probability if
either crosses the configured prob_threshold; otherwise skip.

Training is identical in shape to btc_train_v2 (CatBoost + Optuna +
calibration), only the target changes.

Usage:
  python scripts/btc_train_v7_tpsl.py --interval 1h --lookback-months 36 \
      --tp-pct 0.005 --sl-pct 0.003 --horizon-bars 4 \
      --algorithms catboost --optuna-trials 30 \
      --export-path data/models/v7
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import btc_train_v2 as t  # type: ignore


def build_tpsl_labels(
    candles: list[dict],
    indices: list[int],
    *,
    tp_pct: float,
    sl_pct: float,
    horizon_bars: int,
) -> tuple[np.ndarray, np.ndarray]:
    """For every (i in indices), simulate opening LONG and SHORT at
    candles[i+1].open and watching candles[i+1 ... i+horizon_bars].
    Return two binary arrays aligned with `indices`.
    Skips entries where i+1+horizon_bars >= len(candles)."""
    n = len(indices)
    y_long  = np.zeros(n, dtype=np.int8)
    y_short = np.zeros(n, dtype=np.int8)
    valid   = np.ones(n,  dtype=bool)
    last_idx = len(candles) - 1
    for k, i in enumerate(indices):
        entry_idx = i + 1
        end_idx   = entry_idx + horizon_bars
        if end_idx >= last_idx:
            valid[k] = False
            continue
        entry_px = float(candles[entry_idx]["open"])
        tp_long  = entry_px * (1.0 + tp_pct)
        sl_long  = entry_px * (1.0 - sl_pct)
        tp_short = entry_px * (1.0 - tp_pct)
        sl_short = entry_px * (1.0 + sl_pct)
        # Walk forward bar-by-bar; first crossing decides
        long_outcome = 0   # 0 = neither (treated as loss for the classifier)
        short_outcome = 0
        for j in range(entry_idx, end_idx + 1):
            bar = candles[j]
            hi = float(bar["high"]); lo = float(bar["low"])
            # LONG side
            if long_outcome == 0:
                tp_hit = hi >= tp_long
                sl_hit = lo <= sl_long
                if tp_hit and sl_hit:    long_outcome = -1   # ambiguous → loss
                elif tp_hit:             long_outcome = 1
                elif sl_hit:             long_outcome = -1
            # SHORT side
            if short_outcome == 0:
                tp_hit = lo <= tp_short
                sl_hit = hi >= sl_short
                if tp_hit and sl_hit:    short_outcome = -1
                elif tp_hit:             short_outcome = 1
                elif sl_hit:             short_outcome = -1
            if long_outcome != 0 and short_outcome != 0:
                break
        y_long[k]  = 1 if long_outcome  == 1 else 0
        y_short[k] = 1 if short_outcome == 1 else 0
    return y_long, y_short, valid


def train_one_classifier(X, y, feature_names, optuna_trials, name):
    """Mini wrapper around CatBoost + Optuna lifted from btc_train_v2."""
    try:
        import optuna
        from catboost import CatBoostClassifier, Pool
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:
        raise SystemExit(f"missing dependency: {exc}")

    n = len(X)
    n_train = int(n * 0.70); n_val = int(n * 0.15)
    Xtr, ytr = X[:n_train],            y[:n_train]
    Xva, yva = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
    Xte, yte = X[n_train+n_val:],      y[n_train+n_val:]

    scaler = StandardScaler().fit(Xtr)
    Xtr_s, Xva_s, Xte_s = scaler.transform(Xtr), scaler.transform(Xva), scaler.transform(Xte)

    def objective(trial):
        params = {
            "iterations": 500,
            "depth": trial.suggest_int("depth", 3, 8),
            "learning_rate": trial.suggest_float("lr", 0.01, 0.10, log=True),
            "l2_leaf_reg": trial.suggest_float("l2", 1.0, 10.0),
            "random_strength": trial.suggest_float("rs", 0.0, 5.0),
            "bagging_temperature": trial.suggest_float("bt", 0.0, 1.0),
            "loss_function": "Logloss",
            "verbose": False,
            "random_seed": 42,
        }
        # Single-fold time-series split (no CV — too slow); use val
        cb = CatBoostClassifier(**params)
        cb.fit(Xtr_s, ytr, eval_set=(Xva_s, yva), early_stopping_rounds=20, verbose=False)
        return cb.score(Xva_s, yva)  # accuracy

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=optuna_trials, show_progress_bar=False)
    best = study.best_params
    print(f"  [{name}] Optuna best acc={study.best_value:.4f} params={best}")

    cb = CatBoostClassifier(
        iterations=500, depth=best["depth"], learning_rate=best["lr"],
        l2_leaf_reg=best["l2"], random_strength=best["rs"],
        bagging_temperature=best["bt"],
        loss_function="Logloss", verbose=False, random_seed=42,
    )
    cb.fit(Xtr_s, ytr, eval_set=(Xva_s, yva), early_stopping_rounds=20, verbose=False)

    val_acc  = cb.score(Xva_s, yva)
    test_acc = cb.score(Xte_s, yte)
    val_p   = cb.predict_proba(Xva_s)[:, 1]
    test_p  = cb.predict_proba(Xte_s)[:, 1]

    # Temperature calibration on val
    from scipy.optimize import minimize_scalar
    def nll(T):
        Tc = max(T, 1e-3)
        logit = np.log(np.clip(val_p, 1e-7, 1-1e-7) / np.clip(1-val_p, 1e-7, 1))
        p = 1.0 / (1.0 + np.exp(-logit / Tc))
        return -np.mean(yva * np.log(p + 1e-12) + (1-yva) * np.log(1-p + 1e-12))
    Topt = minimize_scalar(nll, bounds=(0.5, 5.0), method="bounded").x
    print(f"  [{name}] val_acc={val_acc:.4f}  test_acc={test_acc:.4f}  yes_rate={ytr.mean():.4f}  T={Topt:.4f}")

    return {
        "model":     cb,
        "scaler_means": scaler.mean_.tolist(),
        "scaler_stds":  scaler.scale_.tolist(),
        "best_params":  best,
        "val_acc":   float(val_acc),
        "test_acc":  float(test_acc),
        "yes_rate":  float(ytr.mean()),
        "calibration_T": float(Topt),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval",         default="1h")
    ap.add_argument("--lookback-months",  type=int,   default=36)
    ap.add_argument("--tp-pct",           type=float, default=0.005)
    ap.add_argument("--sl-pct",           type=float, default=0.003)
    ap.add_argument("--horizon-bars",     type=int,   default=4)
    ap.add_argument("--optuna-trials",    type=int,   default=30)
    ap.add_argument("--export-path",      type=Path,  default=Path("data/models/v7"))
    args = ap.parse_args()

    print(f"v7 training — TP={args.tp_pct*100:.2f}% SL={args.sl_pct*100:.2f}% "
          f"horizon={args.horizon_bars}h  Optuna trials={args.optuna_trials}")

    print("Loading data + features ...")
    data = t.load_data(args.interval, args.lookback_months)
    X, _y_old = t.build_dataset(data["binance"], data["coinbase"], data["derivatives"])
    indices = list(range(t.REQUIRED_LOOKBACK, len(data["binance"]) - 1))

    print("Building TP-vs-SL labels ...")
    y_long, y_short, valid = build_tpsl_labels(
        data["binance"], indices,
        tp_pct=args.tp_pct, sl_pct=args.sl_pct,
        horizon_bars=args.horizon_bars,
    )
    Xv = np.asarray(X)[valid]
    y_long_v  = y_long[valid]
    y_short_v = y_short[valid]
    print(f"  rows total: {len(X)}  valid: {valid.sum()} ({valid.mean()*100:.1f}%)")
    print(f"  base_rate(LONG_TP_hit)  = {y_long_v.mean():.4f}")
    print(f"  base_rate(SHORT_TP_hit) = {y_short_v.mean():.4f}")

    print("\nTraining LONG-side classifier ...")
    long_res  = train_one_classifier(Xv, y_long_v,  t.FEATURE_NAMES, args.optuna_trials, "LONG")
    print("\nTraining SHORT-side classifier ...")
    short_res = train_one_classifier(Xv, y_short_v, t.FEATURE_NAMES, args.optuna_trials, "SHORT")

    out_dir = Path(args.export_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    long_res["model"].save_model(str(out_dir / "btc_1h_v7_long.cbm"))
    short_res["model"].save_model(str(out_dir / "btc_1h_v7_short.cbm"))

    artefact = {
        "model_name":   "btc_v7_tpsl_classifier",
        "model_version": "v7.0.0",
        "interval":     args.interval,
        "tp_pct":       args.tp_pct,
        "sl_pct":       args.sl_pct,
        "horizon_bars": args.horizon_bars,
        "feature_names": list(t.FEATURE_NAMES),
        "long":  {**{k: v for k, v in long_res.items()  if k != "model"}},
        "short": {**{k: v for k, v in short_res.items() if k != "model"}},
        "long_model":   "btc_1h_v7_long.cbm",
        "short_model":  "btc_1h_v7_short.cbm",
    }
    out_json = out_dir / "btc_1h_v7.json"
    out_json.write_text(json.dumps(artefact, indent=2))
    print(f"\nArtefact: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
