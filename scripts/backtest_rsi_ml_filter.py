"""Mean-reversion RSI + ML filter backtest.

Hypothesis: combining the RSI mean-reversion signal (which alone yields
+$30 / 480 days at WR 60%) with the v7 TP-classifier as a filter should
boost WR by skipping the trades where the ML model thinks TP is unlikely.

  - Generate RSI extreme signals (RSI <= 25 → LONG, RSI >= 75 → SHORT)
  - For each candidate, compute v7 P(TP_hit | side); only enter if
    P >= ml_min_prob.
  - Same fee structure as backtest_v7_diff_fees: open maker, TP=maker,
    SL/time-exit=taker.

If this combination consistently lifts WR above ~62%, the ML model is
adding value as a filter even though it's not profitable as a primary
signal.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import btc_train_v2 as t  # type: ignore
from backtest_v6_realistic import _build_aligned_features  # type: ignore
from backtest_mean_reversion import rsi as rsi_fn, load_candles  # type: ignore


def simulate(
    *,
    candles: list[dict],
    rsi_arr: np.ndarray,
    p_long: np.ndarray,
    p_short: np.ndarray,
    feat_indices: list[int],
    rsi_low: float,
    rsi_high: float,
    ml_min_prob: float,
    tp_pct: float,
    sl_pct: float,
    max_hold_hours: int,
    leverage: int,
    stake_usd: float,
    test_start_idx: int,
) -> list[dict]:
    OPEN_FEE = 0.00015; OPEN_SLIP = 0.0001
    TP_FEE = 0.00015; TP_SLIP = 0.0
    TAKER_FEE = 0.00045; TAKER_SLIP = 0.0005

    # Map candle index → feat index for fast lookup
    feat_map = {ci: fi for fi, ci in enumerate(feat_indices)}

    trades: list[dict] = []
    pos = None
    n = len(candles)
    for i in range(test_start_idx, n - 1):
        bar = candles[i]; next_bar = candles[i + 1]
        hi = float(bar["high"]); lo = float(bar["low"])

        if pos is not None:
            entry = pos["entry_px"]; is_long = pos["is_long"]
            tp_px = entry * (1.0 + tp_pct) if is_long else entry * (1.0 - tp_pct)
            sl_px = entry * (1.0 - sl_pct) if is_long else entry * (1.0 + sl_pct)
            tp_hit = hi >= tp_px if is_long else lo <= tp_px
            sl_hit = lo <= sl_px if is_long else hi >= sl_px
            ex_reason = None; ex_px = None
            if tp_hit and sl_hit: ex_px, ex_reason = sl_px, "sl_pess"
            elif tp_hit:          ex_px, ex_reason = tp_px, "tp"
            elif sl_hit:          ex_px, ex_reason = sl_px, "sl"
            else:
                pos["hold"] += 1
                if pos["hold"] >= max_hold_hours:
                    ex_px, ex_reason = float(bar["close"]), "max_hold"
            if ex_reason:
                ret = (ex_px - entry) / entry if is_long else (entry - ex_px) / entry
                ret -= OPEN_FEE + OPEN_SLIP
                if ex_reason == "tp":
                    ret -= TP_FEE + TP_SLIP
                else:
                    ret -= TAKER_FEE + TAKER_SLIP
                pnl = stake_usd * leverage * ret
                trades.append({
                    "is_long": is_long, "entry_px": entry, "exit_px": ex_px,
                    "ret": ret, "pnl_usd": pnl, "hold_hours": pos["hold"],
                    "exit_reason": ex_reason,
                })
                pos = None

        if pos is None and not np.isnan(rsi_arr[i]):
            r = rsi_arr[i]
            cand_long = r <= rsi_low
            cand_short = r >= rsi_high
            if cand_long or cand_short:
                fi = feat_map.get(i)
                if fi is None: continue
                pl = float(p_long[fi]); ps = float(p_short[fi])
                if cand_long  and pl >= ml_min_prob:
                    pos = {"entry_idx": i + 1, "entry_px": float(next_bar["open"]),
                           "is_long": True, "hold": 0}
                elif cand_short and ps >= ml_min_prob:
                    pos = {"entry_idx": i + 1, "entry_px": float(next_bar["open"]),
                           "is_long": False, "hold": 0}
    return trades


def aggregate(trades):
    if not trades: return {"n": 0}
    p = np.asarray([t["pnl_usd"] for t in trades])
    wins = (p > 0).sum()
    cum = np.cumsum(p); peak = np.maximum.accumulate(cum); mdd = float((peak - cum).max())
    sharpe = float(p.mean() / (p.std() + 1e-12) * np.sqrt(252 * 24)) if len(p) > 1 else 0
    reasons = defaultdict(int)
    for tt in trades: reasons[tt["exit_reason"]] += 1
    return {
        "n": len(trades), "wins": int(wins),
        "wr": round(wins / len(p), 4),
        "total_pnl_usd": round(float(p.sum()), 2),
        "annualised_sharpe": round(sharpe, 2),
        "max_drawdown_usd": round(mdd, 2),
        "avg_hold_hours": round(float(np.mean([tt["hold_hours"] for tt in trades])), 1),
        "exit_reasons": dict(reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v7-model", default="data/models/v7/btc_1h_v7.json")
    ap.add_argument("--lookback-months", type=int, default=36)
    ap.add_argument("--test-fraction", type=float, default=0.30)
    args = ap.parse_args()

    print("Loading data + features ...")
    X, candles, indices, _y = _build_aligned_features("1h", args.lookback_months)
    closes = np.asarray([float(c["close"]) for c in candles])
    rsi14 = rsi_fn(closes, 14)

    art = json.loads(Path(args.v7_model).read_text())
    base = Path(args.v7_model).parent
    from catboost import CatBoostClassifier
    cl = CatBoostClassifier(); cl.load_model(str(base / art["long_model"]))
    cs = CatBoostClassifier(); cs.load_model(str(base / art["short_model"]))
    ml = np.asarray(art["long"]["scaler_means"]); sl = np.asarray(art["long"]["scaler_stds"])
    ms = np.asarray(art["short"]["scaler_means"]); ss = np.asarray(art["short"]["scaler_stds"])
    pl_raw = cl.predict_proba((X - ml) / np.maximum(sl, 1e-8))[:, 1]
    ps_raw = cs.predict_proba((X - ms) / np.maximum(ss, 1e-8))[:, 1]
    def temp(p, T):
        T = max(T, 1e-3); l = np.log(np.clip(p, 1e-7, 1-1e-7) / np.clip(1-p, 1e-7, 1))
        return 1.0 / (1.0 + np.exp(-l / T))
    p_long = temp(pl_raw, art["long"]["calibration_T"])
    p_short = temp(ps_raw, art["short"]["calibration_T"])

    n = len(candles); ts = int(n * (1 - args.test_fraction))
    print(f"Test: {n - ts} bars (~{(n - ts) / 24:.1f} days)")

    print()
    print(f"{'rsi_lo/hi':>10} {'ml_min':>7} {'TP':>5} {'SL':>5} {'hold':>5} {'lev':>4} "
          f"{'trades':>6} {'WR':>6} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'reasons':>22}")
    for lo, hi in [(20, 80), (25, 75), (30, 70)]:
        for ml_min in (0.0, 0.25, 0.30, 0.35, 0.40):
            for tp, sl_ in [(0.008, 0.015), (0.010, 0.020), (0.012, 0.025), (0.015, 0.030)]:
                for max_h in (8, 16, 24):
                    for lev in (3, 5):
                        trs = simulate(
                            candles=candles, rsi_arr=rsi14,
                            p_long=p_long, p_short=p_short,
                            feat_indices=indices,
                            rsi_low=lo, rsi_high=hi, ml_min_prob=ml_min,
                            tp_pct=tp, sl_pct=sl_,
                            max_hold_hours=max_h, leverage=lev,
                            stake_usd=100.0, test_start_idx=ts,
                        )
                        s = aggregate(trs)
                        if s["n"] < 10: continue
                        if s["total_pnl_usd"] < 5: continue   # only show profitable
                        r = "/".join(f"{k[:3]}:{v}" for k, v in (s.get("exit_reasons") or {}).items())[:22]
                        print(f"{lo}/{hi:<7d} {ml_min:>6.2f}  {tp*100:>4.2f}% {sl_*100:>4.2f}% "
                              f"{max_h:>4}h {lev:>3}x {s['n']:>6d} {s['wr']*100:>5.1f}% "
                              f"{s['total_pnl_usd']:>+10.2f} {s['annualised_sharpe']:>7.2f} "
                              f"{s['max_drawdown_usd']:>8.2f} {r:>22}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
