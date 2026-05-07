"""Realistic event-driven backtest for the v7 TP-vs-SL classifier.

Uses both LONG and SHORT side probabilities; takes the side with the
higher prob if it crosses prob_threshold; otherwise skips.  Same
event-driven simulator as backtest_v6_realistic but with the new
side-selection logic.

Crucially, this backtest also evaluates with **MAKER fees** (0.015%)
because v7 is only worth deploying with limit-order entry; with taker
fees v6 already showed the model can't beat frictions.

Usage on the VPS:
  python scripts/backtest_v7_realistic.py \
      --model data/models/v7/btc_1h_v7.json \
      --leverage 3 --prob-threshold 0.55 \
      --fees-pct 0.00015 --slippage-pct 0.0003 \
      --sweep
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import btc_train_v2 as t  # type: ignore
from backtest_v6_realistic import simulate, aggregate, _build_aligned_features  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model",         default="data/models/v7/btc_1h_v7.json")
    ap.add_argument("--interval",      default="1h")
    ap.add_argument("--lookback-months", type=int, default=36)
    ap.add_argument("--leverage",      type=int,   default=3)
    ap.add_argument("--prob-threshold", type=float, default=0.55)
    ap.add_argument("--fees-pct",      type=float, default=0.00015,
                    help="Maker fee on HL = 0.015%; taker = 0.045%")
    ap.add_argument("--slippage-pct",  type=float, default=0.0003)
    ap.add_argument("--stake-usd",     type=float, default=50.0)
    ap.add_argument("--test-fraction", type=float, default=0.15)
    ap.add_argument("--sweep",         action="store_true")
    args = ap.parse_args()

    print("Loading features ...")
    X, candles, indices, _y = _build_aligned_features(args.interval, args.lookback_months)
    print(f"  {len(indices)} bars")

    print(f"Loading v7 artefact: {args.model}")
    artefact = json.loads(Path(args.model).read_text())
    tp_pct = float(artefact["tp_pct"])
    sl_pct = float(artefact["sl_pct"])
    horizon_bars = int(artefact["horizon_bars"])
    print(f"  TP={tp_pct*100:.2f}%  SL={sl_pct*100:.2f}%  horizon={horizon_bars}h")

    base = Path(args.model).parent
    from catboost import CatBoostClassifier
    cb_long  = CatBoostClassifier(); cb_long.load_model(str(base / artefact["long_model"]))
    cb_short = CatBoostClassifier(); cb_short.load_model(str(base / artefact["short_model"]))

    # Apply scaler used at training (per-side scaler — they were fit on the
    # same data so should match, but use long's to be safe)
    means_l = np.asarray(artefact["long"]["scaler_means"])
    stds_l  = np.asarray(artefact["long"]["scaler_stds"])
    means_s = np.asarray(artefact["short"]["scaler_means"])
    stds_s  = np.asarray(artefact["short"]["scaler_stds"])
    Xs_l = (X - means_l) / np.maximum(stds_l, 1e-8)
    Xs_s = (X - means_s) / np.maximum(stds_s, 1e-8)

    p_long_raw  = cb_long.predict_proba(Xs_l)[:, 1]
    p_short_raw = cb_short.predict_proba(Xs_s)[:, 1]

    # Apply temperature calibration
    Tl = float(artefact["long"]["calibration_T"])
    Ts = float(artefact["short"]["calibration_T"])
    def temp(p, Tc):
        Tc = max(Tc, 1e-3)
        logit = np.log(np.clip(p, 1e-7, 1-1e-7) / np.clip(1-p, 1e-7, 1))
        return 1.0 / (1.0 + np.exp(-logit / Tc))
    p_long  = temp(p_long_raw,  Tl)
    p_short = temp(p_short_raw, Ts)

    # Build a synthetic "yes_prob" from the two sides so we can reuse the
    # v6 simulator: prob > 0.5 means LONG, prob < 0.5 means SHORT.
    # Encoding: prob_blend = 0.5 + (p_long - p_short) / 2 (clipped)
    # Effective "edge" = max(p_long, p_short) - threshold
    chosen_prob = np.where(p_long > p_short, 0.5 + (p_long - 0.5), 0.5 - (p_short - 0.5))
    chosen_prob = np.clip(chosen_prob, 0.0, 1.0)
    chosen_max  = np.maximum(p_long, p_short)

    # Filter: only enter when chosen_max > threshold
    # We'll do this by giving the simulator a custom edge_thresh so that the
    # bar's "edge" = |chosen_prob - 0.5| only crosses the gate when
    # max(p_long, p_short) > prob_threshold.
    # Simplest: zero out the prob of bars below threshold (push to 0.5).
    threshold = args.prob_threshold
    mask = chosen_max < threshold
    chosen_prob_masked = np.where(mask, 0.5, chosen_prob)

    n = len(indices)
    test_start = int(n * (1 - args.test_fraction))
    print(f"  Test window: {n - test_start} bars (~{(n - test_start)/24:.1f} days)")
    print(f"  threshold={threshold} → entries above: {(~mask).sum()}/{n} ({(~mask).mean()*100:.1f}%)")

    res = simulate(
        probs=chosen_prob_masked,
        candles=candles, indices=indices,
        leverage=args.leverage,
        tp_pct=tp_pct, sl_pct=sl_pct,
        time_exit_bars=horizon_bars,
        edge_thresh=0.001,  # tiny — already filtered by mask
        fees_pct=args.fees_pct, slippage_pct=args.slippage_pct,
        stake_usd=args.stake_usd, agreement_skip=True,
        test_start_idx=test_start,
    )
    s = aggregate(res["trades"], args.stake_usd)

    print()
    print("=" * 70)
    print(f"REALISTIC BACKTEST — v7 TP/SL classifier")
    print("=" * 70)
    print(f"  TP={tp_pct*100:.2f}%  SL={sl_pct*100:.2f}%  horizon={horizon_bars}h "
          f"leverage={args.leverage}x")
    print(f"  prob_threshold={threshold}  fees={args.fees_pct*100:.3f}%/fill  "
          f"slippage={args.slippage_pct*100:.3f}%/fill")
    print(f"  Trades:   {s.get('n', 0)}")
    if s.get("n", 0) == 0: return 0
    print(f"  Win rate: {s['win_rate']*100:.1f}% ({s['wins']} W / {s['losses']} L)")
    print(f"  Total PnL (net): ${s['total_pnl_usd']:+,.2f}")
    print(f"  Avg PnL / trade: ${s['avg_pnl_usd']:+.4f}")
    print(f"  Sharpe (annualised): {s['annualised_sharpe']:.2f}")
    print(f"  Max drawdown: ${s['max_drawdown_usd']:.2f}")
    print(f"  Avg hold: {s['avg_hold_bars']:.2f} bars")
    print(f"  Exit reasons: {s['exit_reasons']}")

    if args.sweep:
        print()
        print("=" * 70)
        print("THRESHOLD × FEE SCENARIO SWEEP")
        print("=" * 70)
        print(f"{'thr':>5} {'fee%':>6} {'trades':>7} {'WR':>7} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8}")
        for thr in (0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65):
            mask = chosen_max < thr
            chosen_p = np.where(mask, 0.5, chosen_prob)
            for fee in (0.00015, 0.00030, 0.00045):  # maker / midpoint / taker
                r = simulate(
                    probs=chosen_p, candles=candles, indices=indices,
                    leverage=args.leverage, tp_pct=tp_pct, sl_pct=sl_pct,
                    time_exit_bars=horizon_bars, edge_thresh=0.001,
                    fees_pct=fee, slippage_pct=args.slippage_pct,
                    stake_usd=args.stake_usd, agreement_skip=True,
                    test_start_idx=test_start,
                )
                ss = aggregate(r["trades"], args.stake_usd)
                if ss["n"] > 0:
                    print(f"{thr:>5.2f} {fee*100:>5.3f}% {ss['n']:>7d} "
                          f"{ss['win_rate']*100:>6.1f}% {ss['total_pnl_usd']:>+10.2f} "
                          f"{ss['annualised_sharpe']:>7.2f} {ss['max_drawdown_usd']:>8.2f}")
                else:
                    print(f"{thr:>5.2f} {fee*100:>5.3f}%   no trades")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
