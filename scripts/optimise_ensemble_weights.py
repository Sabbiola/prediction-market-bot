"""Find the (w_ml, w_llm) blend weights that maximise live Sharpe / EV.

Reads the meta dataset built by ``build_meta_dataset.py`` and grid-searches
over ensemble weights + agreement multipliers, scoring each combination on
the Polymarket-paper-style outcome (resolved_yes vs blended prediction).

Output:
  - prints a leaderboard of best weights
  - writes data/models/meta/ensemble_weights.json so the ensemble engine
    can pick them up at boot

This is *not* the same as the LightGBM stacker.  The stacker uses 100+
features; this one only uses the per-bot probabilities so the result is
directly applicable to the simple linear blend in
``prediction_ensemble_engine.py``.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError as exc:
    raise SystemExit(f"missing numpy/pandas: {exc}")


def blend(p_ml, p_llm, w_ml, w_llm, boost, damp):
    """Vectorised blend matching prediction_ensemble_engine.combine_ml_llm."""
    p_blend = w_ml * p_ml + w_llm * p_llm
    same_side = (p_ml >= 0.5) == (p_llm >= 0.5)
    multiplier = np.where(same_side, boost, damp)
    p_final = 0.5 + (p_blend - 0.5) * multiplier
    return np.clip(p_final, 0.0, 1.0)


def score(probs, labels, *, edge_thresh=0.0):
    """Win rate when betting only when |p - 0.5| > edge_thresh."""
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
    ap.add_argument("--csv",  type=Path, default=Path("data/meta/btc_15m_meta_train.csv"))
    ap.add_argument("--out",  type=Path, default=Path("data/models/meta/ensemble_weights.json"))
    ap.add_argument("--ml-bot",  default="v5", help="which bot's prob is the ML side (default v5)")
    ap.add_argument("--llm-bot", default="llm", help="which bot's prob is the LLM side (default llm)")
    ap.add_argument("--edge-thresh", type=float, default=0.005)
    args = ap.parse_args()

    csv_path = (args.root / args.csv).resolve()
    out_path = (args.root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}")

    ml_col  = f"pred_{args.ml_bot}_prob"
    llm_col = f"pred_{args.llm_bot}_prob"
    if ml_col not in df.columns or llm_col not in df.columns:
        print(f"  ! missing column: {ml_col=} {llm_col=} (have {df.columns.tolist()[:8]}...)")
        return 1

    df[ml_col]  = pd.to_numeric(df[ml_col],  errors="coerce")
    df[llm_col] = pd.to_numeric(df[llm_col], errors="coerce")
    df = df.dropna(subset=[ml_col, llm_col, "resolved_yes"]).copy()
    n = len(df)
    print(f"  {n} rows with both ML and LLM predictions")
    if n < 50:
        print("  ! too few co-covered rows — keep collecting more live trades")
        return 1

    # Time-based 80/20 split for honest validation
    split = int(n * 0.80)
    train = df.iloc[:split]
    test  = df.iloc[split:]

    p_ml_tr  = train[ml_col].values
    p_llm_tr = train[llm_col].values
    y_tr     = train["resolved_yes"].astype(int).values
    p_ml_te  = test[ml_col].values
    p_llm_te = test[llm_col].values
    y_te     = test["resolved_yes"].astype(int).values
    print(f"  split: train={len(train)}  test={len(test)}")

    # Grid search
    weight_grid = np.linspace(0.0, 1.0, 11)            # 0.0, 0.1, ... 1.0
    boost_grid  = [1.0, 1.10, 1.20, 1.30, 1.50]
    damp_grid   = [0.30, 0.50, 0.70, 0.90]

    rows = []
    for w_ml in weight_grid:
        w_llm = 1.0 - w_ml
        for boost, damp in itertools.product(boost_grid, damp_grid):
            p_blend_tr = blend(p_ml_tr, p_llm_tr, w_ml, w_llm, boost, damp)
            wr_tr, n_used_tr = score(p_blend_tr, y_tr, edge_thresh=args.edge_thresh)
            p_blend_te = blend(p_ml_te, p_llm_te, w_ml, w_llm, boost, damp)
            wr_te, n_used_te = score(p_blend_te, y_te, edge_thresh=args.edge_thresh)
            rows.append({
                "w_ml":      round(w_ml, 2),
                "w_llm":     round(w_llm, 2),
                "boost":     boost,
                "damp":      damp,
                "wr_train":  round(wr_tr, 4),
                "n_train":   n_used_tr,
                "wr_test":   round(wr_te, 4),
                "n_test":    n_used_te,
            })

    res = pd.DataFrame(rows)
    # Score by held-out test WR; require at least 20 trades on test to be meaningful
    valid = res[res["n_test"] >= 20].copy()
    if valid.empty:
        valid = res.copy()
    valid = valid.sort_values("wr_test", ascending=False)

    print("\n=== Top 10 weight combos by test WR ===")
    print(f"{'w_ml':>5} {'w_llm':>6} {'boost':>6} {'damp':>5} {'wr_tr':>7} {'wr_te':>7} {'n_tr':>5} {'n_te':>5}")
    for _, r in valid.head(10).iterrows():
        print(f"{r['w_ml']:>5.2f} {r['w_llm']:>6.2f} {r['boost']:>6.2f} {r['damp']:>5.2f} "
              f"{r['wr_train']:>7.4f} {r['wr_test']:>7.4f} {int(r['n_train']):>5d} {int(r['n_test']):>5d}")

    best = valid.iloc[0]
    payload = {
        "ml_bot":            args.ml_bot,
        "llm_bot":           args.llm_bot,
        "edge_thresh":       args.edge_thresh,
        "samples_total":     n,
        "samples_train":     len(train),
        "samples_test":      len(test),
        "best": {
            "w_ml":  float(best["w_ml"]),
            "w_llm": float(best["w_llm"]),
            "agreement_boost":   float(best["boost"]),
            "disagreement_damp": float(best["damp"]),
        },
        "best_metrics": {
            "wr_train":   float(best["wr_train"]),
            "wr_test":    float(best["wr_test"]),
            "n_train_used": int(best["n_train"]),
            "n_test_used":  int(best["n_test"]),
        },
        "default_baseline": {
            "w_ml": 0.60, "w_llm": 0.40, "agreement_boost": 1.20, "disagreement_damp": 0.50,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out_path}")
    print(f"\nRecommended ensemble weights:")
    print(f"  ensemble_weight_ml: {payload['best']['w_ml']:.2f}")
    print(f"  ensemble_weight_llm: {payload['best']['w_llm']:.2f}")
    print(f"  ensemble_agreement_boost: {payload['best']['agreement_boost']:.2f}")
    print(f"  ensemble_disagreement_damp: {payload['best']['disagreement_damp']:.2f}")
    print(f"  → test WR {payload['best_metrics']['wr_test']:.4f} on {payload['best_metrics']['n_test_used']} trades")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
