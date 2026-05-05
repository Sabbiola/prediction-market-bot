"""Train a LightGBM stacker over the per-bot probabilities + raw features.

Input:  data/meta/btc_15m_meta_train.csv  (built by build_meta_dataset.py)
Output: data/models/meta/btc_15m_meta.json + .lgb

The stacker outputs a calibrated YES probability that combines:
  - v4/v5/v6 ML predictions (when available)
  - LLM Llama-3.3 prediction
  - HL-perp model E prediction
  - the raw runtime features (RSI, returns, CB lead-lag, etc.)

Time-based 70/15/15 train/val/test split (chronological by market_id ordering
in the CSV — markets close in arrival order, so this approximates time).

We also report:
  - per-bot baseline accuracy on the test set
  - stacker accuracy and Brier score
  - simple expected-value calculation: assuming the same 0.30%/0.20% TP/SL
    structure as Bot E (break-even WR=40%), how does the stacker's WR change
    if we only trade when |stacker_prob - 0.5| > threshold?
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"missing numpy/pandas: {exc}")

try:
    import lightgbm as lgb
except ImportError:
    raise SystemExit("missing lightgbm; pip install lightgbm")


def brier(y_true, y_prob):
    return float(np.mean((np.asarray(y_prob) - np.asarray(y_true)) ** 2))


def accuracy_at_threshold(probs, labels, *, edge_thresh=0.0):
    """Win rate when betting only when |p - 0.5| > edge_thresh, betting YES if p>0.5 else NO."""
    p = np.asarray(probs)
    y = np.asarray(labels)
    mask = np.abs(p - 0.5) > edge_thresh
    if mask.sum() == 0:
        return 0.0, 0
    side_yes = p[mask] > 0.5
    win = ((side_yes & (y[mask] == 1)) | (~side_yes & (y[mask] == 0))).mean()
    return float(win), int(mask.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("/opt/pmbot"))
    ap.add_argument("--csv", type=Path, default=Path("data/meta/btc_15m_meta_train.csv"))
    ap.add_argument("--out", type=Path, default=Path("data/models/meta/btc_15m_meta.json"))
    ap.add_argument("--lgb-out", type=Path, default=Path("data/models/meta/btc_15m_meta.lgb"))
    args = ap.parse_args()

    root = args.root.resolve()
    csv_path = (root / args.csv).resolve()
    out_path = (root / args.out).resolve()
    lgb_path = (root / args.lgb_out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Loading {csv_path} ...")
    df = pd.read_csv(csv_path)
    print(f"  rows={len(df)}  cols={len(df.columns)}")

    # Drop side string cols (not numeric features for LGBM here)
    for col in [c for c in df.columns if c.endswith("_side")]:
        df = df.drop(columns=[col])

    # Cast all expected numeric columns to float; empty strings become NaN
    for c in df.columns:
        if c == "market_id":
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")

    y = df["resolved_yes"].astype(int).values
    market_ids = df["market_id"].values
    feature_cols = [c for c in df.columns if c not in ("market_id", "resolved_yes")]
    X = df[feature_cols].values
    print(f"  features: {len(feature_cols)}  yes_rate={y.mean():.4f}")

    n = len(df)
    n_train = int(n * 0.70)
    n_val   = int(n * 0.15)
    Xtr, ytr = X[:n_train],            y[:n_train]
    Xva, yva = X[n_train:n_train+n_val], y[n_train:n_train+n_val]
    Xte, yte = X[n_train+n_val:],      y[n_train+n_val:]
    print(f"  split: train={len(Xtr)}  val={len(Xva)}  test={len(Xte)}")

    if len(Xtr) < 50 or len(Xte) < 20:
        print("  ! sample size too small for a meaningful stack — keeping output but expect noisy metrics")

    # Per-bot baselines on test
    print("\n=== Per-bot baseline (test set) ===")
    print(f"{'bot':<6} {'acc':>8} {'brier':>8} {'covered':>8}")
    bot_baselines: dict[str, dict] = {}
    for lbl in ("v4", "v5", "v6", "llm", "e"):
        col = f"pred_{lbl}_prob"
        if col not in df.columns:
            continue
        probs_te = df[col].values[n_train+n_val:]
        mask = ~pd.isna(probs_te)
        covered = int(mask.sum())
        if covered == 0:
            print(f"{lbl:<6}  no coverage")
            continue
        acc = float(((probs_te[mask] > 0.5) == (yte[mask] == 1)).mean())
        b = brier(yte[mask], probs_te[mask])
        print(f"{lbl:<6} {acc:>8.4f} {b:>8.4f} {covered:>8d}")
        bot_baselines[lbl] = {"acc": acc, "brier": b, "covered": covered}

    # Train LGBM stacker
    print("\n=== Training LightGBM stacker ===")
    train_set = lgb.Dataset(Xtr, ytr, feature_name=feature_cols, free_raw_data=False)
    val_set   = lgb.Dataset(Xva, yva, feature_name=feature_cols, free_raw_data=False, reference=train_set)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.02,
        "num_leaves": 31,
        "min_data_in_leaf": max(20, len(Xtr)//50),
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 5,
        "verbose": -1,
        "lambda_l2": 1.0,
    }
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=2000,
        valid_sets=[train_set, val_set],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(100)],
    )

    p_te = booster.predict(Xte, num_iteration=booster.best_iteration)
    acc_te = float(((p_te > 0.5) == (yte == 1)).mean())
    br_te  = brier(yte, p_te)
    print(f"\nStacker test: acc={acc_te:.4f}  brier={br_te:.4f}")

    print("\n=== Threshold sensitivity (stacker on test set) ===")
    print(f"{'edge_thresh':>12} {'trades':>8} {'WR':>8} {'EV(0.30/0.20)':>14}")
    threshold_table = []
    for thr in (0.00, 0.005, 0.01, 0.02, 0.03, 0.05):
        wr, n_used = accuracy_at_threshold(p_te, yte, edge_thresh=thr)
        # EV at HL TP=0.30%, SL=0.20% per trade, leverage=3x
        ev_per_trade = wr * 0.0030 - (1 - wr) * 0.0020
        ev_lev = ev_per_trade * 3
        marker = " ←" if thr == 0.01 else ""
        print(f"{thr:>12.4f} {n_used:>8d} {wr:>8.4f} {ev_lev:>14.4%}{marker}")
        threshold_table.append({"edge_thresh": thr, "trades": n_used, "wr": wr, "ev_3x_lev": ev_lev})

    # Feature importance
    importance = sorted(
        zip(booster.feature_name(), booster.feature_importance(importance_type="gain")),
        key=lambda x: x[1], reverse=True,
    )
    print("\n=== Top 15 features by gain ===")
    for name, imp in importance[:15]:
        print(f"  {imp:>10.1f}  {name}")

    booster.save_model(str(lgb_path), num_iteration=booster.best_iteration)
    artifact = {
        "model_name": "btc_meta_stack_v1",
        "model_version": "v1.0.0",
        "feature_columns": feature_cols,
        "best_iteration": booster.best_iteration,
        "test": {"acc": acc_te, "brier": br_te, "samples": len(yte)},
        "per_bot_baseline": bot_baselines,
        "threshold_table": threshold_table,
        "top_features": [{"name": n, "gain": int(g)} for n, g in importance[:20]],
        "lgb_model": str(lgb_path),
    }
    out_path.write_text(json.dumps(artifact, indent=2))
    print(f"\nArtifact: {out_path}")
    print(f"LGBM:     {lgb_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
