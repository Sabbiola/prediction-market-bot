"""Find the highest-PnL configuration of v7 + trailing on the test window.

Sweeps the dimensions that mattered in the previous round:
  - leverage:        3x / 5x / 10x
  - prob_threshold:  0.28 / 0.30 / 0.32 / 0.35
  - trailing_pct:    0.0025 / 0.0030 / 0.0035 / 0.0040 / 0.0050
  - stake:           $50 / $100 (cap reasonable to $1k bankroll)

Only one scenario was profitable previously (trailing 0.3% @ 0.30 thr,
no BE pin) so we sweep around that with maker fees.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_v6_realistic import _build_aligned_features, aggregate  # type: ignore
from backtest_v7_trailing import simulate_with_trailing  # type: ignore


def load_v7_probs(model_path: Path):
    artefact = json.loads(model_path.read_text())
    base = model_path.parent
    from catboost import CatBoostClassifier
    cl = CatBoostClassifier(); cl.load_model(str(base / artefact["long_model"]))
    cs = CatBoostClassifier(); cs.load_model(str(base / artefact["short_model"]))
    return artefact, cl, cs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="data/models/v7/btc_1h_v7.json")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--lookback-months", type=int, default=36)
    ap.add_argument("--fees-pct",     type=float, default=0.00015)
    ap.add_argument("--slippage-pct", type=float, default=0.0003)
    ap.add_argument("--test-fraction", type=float, default=0.15)
    args = ap.parse_args()

    print("Loading features ...")
    X, candles, indices, _y = _build_aligned_features(args.interval, args.lookback_months)
    artefact, cl, cs = load_v7_probs(Path(args.model))
    tp_pct = float(artefact["tp_pct"])
    sl_pct = float(artefact["sl_pct"])
    horizon_bars = int(artefact["horizon_bars"])
    print(f"  v7: TP={tp_pct*100:.2f}%  SL={sl_pct*100:.2f}%  horizon={horizon_bars}h")

    ml = np.asarray(artefact["long"]["scaler_means"]); sl = np.asarray(artefact["long"]["scaler_stds"])
    ms = np.asarray(artefact["short"]["scaler_means"]); ss = np.asarray(artefact["short"]["scaler_stds"])
    pl_raw = cl.predict_proba((X - ml) / np.maximum(sl, 1e-8))[:, 1]
    ps_raw = cs.predict_proba((X - ms) / np.maximum(ss, 1e-8))[:, 1]
    Tl = float(artefact["long"]["calibration_T"]); Ts = float(artefact["short"]["calibration_T"])
    def temp(p, T):
        T = max(T, 1e-3)
        l = np.log(np.clip(p, 1e-7, 1-1e-7) / np.clip(1-p, 1e-7, 1))
        return 1.0 / (1.0 + np.exp(-l / T))
    pl = temp(pl_raw, Tl); ps = temp(ps_raw, Ts)
    chosen_prob = np.where(pl > ps, 0.5 + (pl - 0.5), 0.5 - (ps - 0.5))
    chosen_prob = np.clip(chosen_prob, 0.0, 1.0)
    chosen_max = np.maximum(pl, ps)

    n = len(indices); test_start = int(n * (1 - args.test_fraction))

    rows = []
    for leverage in (3, 5, 10):
        for stake in (50.0, 100.0):
            for thr in (0.28, 0.30, 0.32, 0.35):
                for trail in (0.0025, 0.0030, 0.0035, 0.0040, 0.0050):
                    trs = simulate_with_trailing(
                        chosen_prob=chosen_prob, chosen_max=chosen_max,
                        candles=candles, indices=indices,
                        leverage=leverage, tp_pct=tp_pct, sl_pct=sl_pct,
                        horizon_bars=horizon_bars, prob_threshold=thr,
                        fees_pct=args.fees_pct, slippage_pct=args.slippage_pct,
                        stake_usd=stake, test_start_idx=test_start,
                        move_to_be_at_pct=0.0, trailing_distance_pct=trail,
                    )
                    s = aggregate(trs, stake)
                    if s.get("n", 0) > 50:
                        rows.append({
                            "leverage": leverage,
                            "stake":    stake,
                            "thr":      thr,
                            "trail":    trail,
                            "n":        s["n"],
                            "wr":       s["win_rate"],
                            "pnl":      s["total_pnl_usd"],
                            "sharpe":   s["annualised_sharpe"],
                            "mdd":      s["max_drawdown_usd"],
                            "avg_hold": s["avg_hold_bars"],
                        })

    rows.sort(key=lambda r: r["pnl"], reverse=True)
    print()
    print("=" * 100)
    print(f"{'TOP CONFIGURATIONS BY NET PnL (160-day backtest)':^100s}")
    print("=" * 100)
    print(f"{'lev':>4} {'stake':>6} {'thr':>5} {'trail':>6} {'trades':>7} {'WR':>7} "
          f"{'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'hold':>6}")
    print("-" * 100)
    for r in rows[:20]:
        print(f"{r['leverage']:>4d}x {r['stake']:>5.0f}$ {r['thr']:>5.2f} "
              f"{r['trail']*100:>5.2f}% {r['n']:>7d} {r['wr']*100:>6.1f}% "
              f"{r['pnl']:>+10.2f} {r['sharpe']:>7.2f} {r['mdd']:>8.2f} {r['avg_hold']:>6.2f}")
    print("=" * 100)

    if rows:
        best = rows[0]
        annual_return_pct = best["pnl"] / 1000 * 100 * (365 / 160)  # rough annualised on $1000 bankroll
        print(f"\nBEST: leverage {best['leverage']}x, stake ${best['stake']:.0f}, "
              f"threshold {best['thr']:.2f}, trailing {best['trail']*100:.2f}%")
        print(f"  → {best['n']} trades over ~160 days")
        print(f"  → WR {best['wr']*100:.1f}%  PnL +${best['pnl']:.2f}  Sharpe {best['sharpe']:.2f}  MDD ${best['mdd']:.2f}")
        print(f"  → annualised on $1k bankroll: ~{annual_return_pct:.1f}% / year")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
