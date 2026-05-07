"""Realistic backtest of v7 TP/SL classifier with TRAILING STOP variants.

Same event-driven simulator as backtest_v6_realistic, plus:
  - move_to_be_at_pct: when price moves x% in favour, SL pinned to entry
  - trailing_distance_pct: SL trails the favourable extreme by x%
  - both can be combined (move-to-BE first, then trailing)

The simulator processes future bars in chronological order and resolves
TP / SL / time-exit as soon as ANY rule fires.  Trailing is updated bar
by bar based on each future bar's high (LONG) or low (SHORT).

Output: side-by-side comparison of {flat SL, BE-only, trailing, BE+trailing}
across probability thresholds.

Usage on the VPS:
  python scripts/backtest_v7_trailing.py \\
      --model data/models/v7/btc_1h_v7.json \\
      --leverage 3 --fees-pct 0.00015 \\
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
from backtest_v6_realistic import _build_aligned_features, aggregate  # type: ignore


def simulate_with_trailing(
    *,
    chosen_prob: np.ndarray,
    chosen_max: np.ndarray,
    candles: list,
    indices: list,
    leverage: int,
    tp_pct: float,
    sl_pct: float,
    horizon_bars: int,
    prob_threshold: float,
    fees_pct: float,
    slippage_pct: float,
    stake_usd: float,
    test_start_idx: int,
    move_to_be_at_pct: float = 0.0,
    trailing_distance_pct: float = 0.0,
) -> list[dict]:
    """Walk-forward simulator with optional move-to-breakeven and trailing.

    move_to_be_at_pct: 0 disables; otherwise when price reaches
        entry × (1 ± x), SL is pinned to entry.
    trailing_distance_pct: 0 disables; otherwise SL trails the running
        favourable extreme by that distance.  Combined with BE: trailing
        only kicks in once BE has been reached.

    Pessimistic tie-break unchanged: if a bar high >= TP and bar low <= SL
    in the same candle, treat it as SL hit first.
    """
    trades: list[dict] = []
    open_pos = None
    n_idx = len(indices)
    last_idx = len(candles) - 1

    for i in range(test_start_idx, n_idx):
        candle_idx = indices[i]

        if open_pos is None:
            if chosen_max[i] < prob_threshold:
                continue
            is_long = chosen_prob[i] > 0.5
            entry_idx = candle_idx + 1
            if entry_idx > last_idx:
                break
            entry_px = float(candles[entry_idx]["open"])

            tp_px = entry_px * (1.0 + tp_pct) if is_long else entry_px * (1.0 - tp_pct)
            sl_px = entry_px * (1.0 - sl_pct) if is_long else entry_px * (1.0 + sl_pct)

            open_pos = {
                "is_long":   is_long,
                "entry_px":  entry_px,
                "entry_idx": entry_idx,
                "tp_px":     tp_px,
                "sl_px":     sl_px,
                "best_px":   entry_px,           # tracked for trailing
                "be_armed":  False,              # has BE been reached yet
            }
            continue

        # We have an open position; walk forward until exit
        # Process current candle (bar candle_idx)
        bar = candles[candle_idx]
        hi = float(bar["high"]); lo = float(bar["low"])
        is_long  = open_pos["is_long"]
        entry_px = open_pos["entry_px"]
        entry_idx = open_pos["entry_idx"]
        tp_px = open_pos["tp_px"]
        sl_px = open_pos["sl_px"]

        # Skip the bar in which we opened (entry happens AT its open; we
        # only check exits from bars AFTER entry's open)
        if candle_idx < entry_idx:
            continue

        # ── Update favourable extreme ──────────────────────────────
        if is_long:
            open_pos["best_px"] = max(open_pos["best_px"], hi)
        else:
            open_pos["best_px"] = min(open_pos["best_px"], lo)
        best = open_pos["best_px"]

        # ── Move SL to break-even if armed ─────────────────────────
        if move_to_be_at_pct > 0 and not open_pos["be_armed"]:
            be_target = entry_px * (1.0 + move_to_be_at_pct) if is_long \
                        else entry_px * (1.0 - move_to_be_at_pct)
            if (is_long and best >= be_target) or (not is_long and best <= be_target):
                # Snap SL up to entry (LONG) or down to entry (SHORT)
                if is_long:
                    sl_px = max(sl_px, entry_px)
                else:
                    sl_px = min(sl_px, entry_px)
                open_pos["sl_px"] = sl_px
                open_pos["be_armed"] = True

        # ── Trailing stop ──────────────────────────────────────────
        if trailing_distance_pct > 0 and (open_pos["be_armed"] or move_to_be_at_pct == 0):
            new_sl = best * (1.0 - trailing_distance_pct) if is_long \
                     else best * (1.0 + trailing_distance_pct)
            if is_long:
                sl_px = max(sl_px, new_sl)
            else:
                sl_px = min(sl_px, new_sl)
            open_pos["sl_px"] = sl_px

        # ── Check exits ────────────────────────────────────────────
        tp_hit = hi >= tp_px if is_long else lo <= tp_px
        sl_hit = lo <= sl_px if is_long else hi >= sl_px

        exit_px = None; exit_reason = None
        if tp_hit and sl_hit:
            exit_px, exit_reason = sl_px, "sl_pessimistic"
        elif tp_hit:
            exit_px, exit_reason = tp_px, "tp"
        elif sl_hit:
            exit_px, exit_reason = sl_px, "sl"
        else:
            hold = candle_idx - entry_idx + 1
            if hold >= horizon_bars:
                exit_px, exit_reason = float(bar["close"]), "time_exit"

        if exit_px is None:
            continue

        # ── Compute net PnL (slippage on entry + exit, fees on each) ──
        ret_pct = (exit_px - entry_px) / entry_px if is_long else (entry_px - exit_px) / entry_px
        ret_pct -= 2 * slippage_pct
        ret_after_fees = ret_pct - 2 * fees_pct
        notional = stake_usd * leverage
        pnl_usd = notional * ret_after_fees

        trades.append({
            "open_idx":        entry_idx,
            "close_idx":       candle_idx,
            "hold_bars":       candle_idx - entry_idx + 1,
            "is_long":         is_long,
            "entry_px":        entry_px,
            "exit_px":         exit_px,
            "ret_pct":         ret_pct,
            "ret_after_fees":  ret_after_fees,
            "pnl_usd":         pnl_usd,
            "exit_reason":     exit_reason,
            "be_armed":        open_pos["be_armed"],
            "leverage":        leverage,
        })
        open_pos = None

    return trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model",         default="data/models/v7/btc_1h_v7.json")
    ap.add_argument("--interval",      default="1h")
    ap.add_argument("--lookback-months", type=int, default=36)
    ap.add_argument("--leverage",      type=int,   default=3)
    ap.add_argument("--fees-pct",      type=float, default=0.00015)
    ap.add_argument("--slippage-pct",  type=float, default=0.0003)
    ap.add_argument("--stake-usd",     type=float, default=50.0)
    ap.add_argument("--test-fraction", type=float, default=0.15)
    ap.add_argument("--sweep",         action="store_true")
    args = ap.parse_args()

    print("Loading features ...")
    X, candles, indices, _y = _build_aligned_features(args.interval, args.lookback_months)

    artefact = json.loads(Path(args.model).read_text())
    tp_pct = float(artefact["tp_pct"])
    sl_pct = float(artefact["sl_pct"])
    horizon_bars = int(artefact["horizon_bars"])

    base = Path(args.model).parent
    from catboost import CatBoostClassifier
    cb_long  = CatBoostClassifier(); cb_long.load_model(str(base / artefact["long_model"]))
    cb_short = CatBoostClassifier(); cb_short.load_model(str(base / artefact["short_model"]))

    means_l = np.asarray(artefact["long"]["scaler_means"]); stds_l = np.asarray(artefact["long"]["scaler_stds"])
    means_s = np.asarray(artefact["short"]["scaler_means"]); stds_s = np.asarray(artefact["short"]["scaler_stds"])
    Xs_l = (X - means_l) / np.maximum(stds_l, 1e-8)
    Xs_s = (X - means_s) / np.maximum(stds_s, 1e-8)
    p_long_raw  = cb_long.predict_proba(Xs_l)[:, 1]
    p_short_raw = cb_short.predict_proba(Xs_s)[:, 1]
    Tl = float(artefact["long"]["calibration_T"]); Ts = float(artefact["short"]["calibration_T"])
    def temp(p, Tc):
        Tc = max(Tc, 1e-3)
        logit = np.log(np.clip(p, 1e-7, 1-1e-7) / np.clip(1-p, 1e-7, 1))
        return 1.0 / (1.0 + np.exp(-logit / Tc))
    p_long  = temp(p_long_raw,  Tl)
    p_short = temp(p_short_raw, Ts)

    chosen_prob = np.where(p_long > p_short, 0.5 + (p_long - 0.5), 0.5 - (p_short - 0.5))
    chosen_prob = np.clip(chosen_prob, 0.0, 1.0)
    chosen_max  = np.maximum(p_long, p_short)

    n = len(indices)
    test_start = int(n * (1 - args.test_fraction))
    print(f"  test bars={n - test_start} (~{(n-test_start)/24:.1f} days)")
    print(f"  v7: TP={tp_pct*100:.2f}%  SL={sl_pct*100:.2f}%  horizon={horizon_bars}h")

    scenarios = [
        ("flat SL",          {"move_to_be_at_pct": 0.0,    "trailing_distance_pct": 0.0}),
        ("BE @+0.3%",         {"move_to_be_at_pct": 0.003,  "trailing_distance_pct": 0.0}),
        ("BE @+0.4%",         {"move_to_be_at_pct": 0.004,  "trailing_distance_pct": 0.0}),
        ("trailing 0.3%",    {"move_to_be_at_pct": 0.0,    "trailing_distance_pct": 0.003}),
        ("trailing 0.5%",    {"move_to_be_at_pct": 0.0,    "trailing_distance_pct": 0.005}),
        ("BE +0.3% + trail 0.3%", {"move_to_be_at_pct": 0.003, "trailing_distance_pct": 0.003}),
        ("BE +0.4% + trail 0.4%", {"move_to_be_at_pct": 0.004, "trailing_distance_pct": 0.004}),
    ]
    thresholds = [0.50, 0.52, 0.55, 0.58, 0.60] if args.sweep else [0.55]

    print("\n" + "=" * 96)
    print("TRAILING-STOP SCENARIO SWEEP — v7 TP/SL classifier")
    print("=" * 96)
    print(f"{'scenario':<30} {'thr':>5} {'trades':>7} {'WR':>7} "
          f"{'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'tp/sl/be/te':>13}")
    for thr in thresholds:
        for label, kw in scenarios:
            trs = simulate_with_trailing(
                chosen_prob=chosen_prob, chosen_max=chosen_max,
                candles=candles, indices=indices,
                leverage=args.leverage, tp_pct=tp_pct, sl_pct=sl_pct,
                horizon_bars=horizon_bars, prob_threshold=thr,
                fees_pct=args.fees_pct, slippage_pct=args.slippage_pct,
                stake_usd=args.stake_usd, test_start_idx=test_start,
                **kw,
            )
            s = aggregate(trs, args.stake_usd)
            if s["n"] == 0:
                print(f"{label:<30} {thr:>5.2f}   no trades")
                continue
            reasons = s.get("exit_reasons", {})
            mix = (f"{reasons.get('tp',0)}/{reasons.get('sl',0) + reasons.get('sl_pessimistic',0)}/"
                   f"{sum(1 for t in trs if t.get('be_armed'))}/{reasons.get('time_exit',0)}")
            print(f"{label:<30} {thr:>5.2f} {s['n']:>7d} "
                  f"{s['win_rate']*100:>6.1f}% {s['total_pnl_usd']:>+10.2f} "
                  f"{s['annualised_sharpe']:>7.2f} {s['max_drawdown_usd']:>8.2f} {mix:>13}")
        if args.sweep:
            print("-" * 96)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
