"""v7 backtest with differentiated fees per exit reason:
   - OPEN:  maker limit (0.015% fee, near-zero slippage)
   - CLOSE: maker IF reason='tp' (limit hit), taker IF sl / time_exit
This is the realistic fee structure for an HL bot using:
   * limit-GTT entry at best bid/ask
   * limit-GTT take-profit
   * trigger-market stop-loss + time-exit fallback
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_v6_realistic import _build_aligned_features  # type: ignore
from backtest_v7_trailing import simulate_with_trailing    # type: ignore


def main() -> int:
    X, candles, indices, _y = _build_aligned_features("1h", 36)
    art = json.loads(Path("/opt/pmbot/data/models/v7/btc_1h_v7.json").read_text())
    from catboost import CatBoostClassifier
    cl = CatBoostClassifier(); cl.load_model("/opt/pmbot/data/models/v7/btc_1h_v7_long.cbm")
    cs = CatBoostClassifier(); cs.load_model("/opt/pmbot/data/models/v7/btc_1h_v7_short.cbm")
    ml = np.asarray(art["long"]["scaler_means"]); sl = np.asarray(art["long"]["scaler_stds"])
    ms = np.asarray(art["short"]["scaler_means"]); ss = np.asarray(art["short"]["scaler_stds"])
    pl_raw = cl.predict_proba((X - ml) / np.maximum(sl, 1e-8))[:, 1]
    ps_raw = cs.predict_proba((X - ms) / np.maximum(ss, 1e-8))[:, 1]

    def temp(p, T):
        T = max(T, 1e-3)
        l = np.log(np.clip(p, 1e-7, 1 - 1e-7) / np.clip(1 - p, 1e-7, 1))
        return 1.0 / (1.0 + np.exp(-l / T))
    pl = temp(pl_raw, art["long"]["calibration_T"])
    ps = temp(ps_raw, art["short"]["calibration_T"])
    chosen_prob = np.where(pl > ps, 0.5 + (pl - 0.5), 0.5 - (ps - 0.5))
    chosen_prob = np.clip(chosen_prob, 0.0, 1.0)
    chosen_max = np.maximum(pl, ps)
    n = len(indices); ts = int(n * 0.85)

    OPEN_FEE = 0.00015
    OPEN_SLIP = 0.0001
    TP_FEE = 0.00015
    TP_SLIP = 0.0
    TAKER_FEE = 0.00045
    TAKER_SLIP = 0.0005

    print(f"{'lev':>4} {'thr':>5} {'trades':>7} {'WR':>7} {'pnl_$':>10} {'sharpe':>7}  reasons")
    print("-" * 78)
    for lev in (3, 5, 10):
        for thr in (0.28, 0.30, 0.32, 0.35):
            trs = simulate_with_trailing(
                chosen_prob=chosen_prob, chosen_max=chosen_max,
                candles=candles, indices=indices, leverage=lev,
                tp_pct=art["tp_pct"], sl_pct=art["sl_pct"],
                horizon_bars=art["horizon_bars"], prob_threshold=thr,
                fees_pct=0.0, slippage_pct=0.0,
                stake_usd=100.0, test_start_idx=ts,
                move_to_be_at_pct=0.0, trailing_distance_pct=0.0,
            )
            if not trs:
                continue
            net = []
            reasons = {}
            wins = 0
            for tr in trs:
                entry = tr["entry_px"]; exit_ = tr["exit_px"]
                is_long = tr["is_long"]; reason = tr["exit_reason"]
                ret = (exit_ - entry) / entry if is_long else (entry - exit_) / entry
                ret -= OPEN_SLIP + OPEN_FEE
                if reason == "tp":
                    ret -= TP_FEE + TP_SLIP
                else:
                    ret -= TAKER_FEE + TAKER_SLIP
                pnl = 100.0 * lev * ret
                net.append(pnl)
                reasons[reason] = reasons.get(reason, 0) + 1
                if pnl > 0: wins += 1
            arr = np.asarray(net)
            wr = wins / len(net) if net else 0
            sharpe = float(arr.mean() / (arr.std() + 1e-12) * np.sqrt(252 * 24)) if len(net) > 1 else 0
            rstr = "/".join(f"{k}:{v}" for k, v in reasons.items())
            print(f"{lev:>3}x {thr:>5.2f} {len(net):>7d} {wr*100:>6.1f}% {arr.sum():>+10.2f} "
                  f"{sharpe:>7.2f}  {rstr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
