"""Backtest RSI mean-reversion on ETH and SOL (no ML filter).

The v7 ML model was trained on BTC features only — applying it to ETH/SOL
would feed it out-of-distribution inputs and produce noise.  For multi-asset
expansion of Bot G we test the pure RSI strategy first; if profitable on ETH
or SOL with realistic frictions, deploy that and worry about an ML filter
trained per asset later.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_mean_reversion import rsi as rsi_fn, load_candles  # type: ignore


OPEN_FEE = 0.00015
OPEN_SLIP = 0.0001
TP_FEE = 0.00015
TP_SLIP = 0.0
TAKER_FEE = 0.00045
TAKER_SLIP = 0.0005


def simulate(candles, rsi_arr, *, rsi_low, rsi_high, tp_pct, sl_pct,
             max_hold, leverage, stake_usd, test_start_idx):
    trades = []
    pos = None
    for i in range(test_start_idx, len(candles) - 1):
        bar = candles[i]; nb = candles[i + 1]
        hi = float(bar["high"]); lo = float(bar["low"])
        if pos is not None:
            entry = pos["entry"]; il = pos["is_long"]
            tp_px = entry * (1 + tp_pct) if il else entry * (1 - tp_pct)
            sl_px = entry * (1 - sl_pct) if il else entry * (1 + sl_pct)
            tp_hit = hi >= tp_px if il else lo <= tp_px
            sl_hit = lo <= sl_px if il else hi >= sl_px
            ex_reason = None; ex_px = None
            if tp_hit and sl_hit:
                ex_px, ex_reason = sl_px, "sl_pess"
            elif tp_hit:
                ex_px, ex_reason = tp_px, "tp"
            elif sl_hit:
                ex_px, ex_reason = sl_px, "sl"
            else:
                pos["hold"] += 1
                if pos["hold"] >= max_hold:
                    ex_px, ex_reason = float(bar["close"]), "max_hold"
            if ex_reason:
                ret = (ex_px - entry) / entry if il else (entry - ex_px) / entry
                ret -= OPEN_SLIP + OPEN_FEE
                if ex_reason == "tp":
                    ret -= TP_FEE + TP_SLIP
                else:
                    ret -= TAKER_FEE + TAKER_SLIP
                pnl = stake_usd * leverage * ret
                trades.append({"pnl": pnl, "reason": ex_reason, "il": il, "hold": pos["hold"]})
                pos = None
        if pos is None and not np.isnan(rsi_arr[i]):
            r = rsi_arr[i]
            if r <= rsi_low:
                pos = {"entry": float(nb["open"]), "is_long": True, "hold": 0}
            elif r >= rsi_high:
                pos = {"entry": float(nb["open"]), "is_long": False, "hold": 0}
    return trades


def agg(trs, days):
    if not trs:
        return None
    p = np.asarray([t["pnl"] for t in trs])
    wins = (p > 0).sum()
    sharpe = float(p.mean() / (p.std() + 1e-12) * np.sqrt(252 * 24)) if len(p) > 1 else 0
    cum = np.cumsum(p); peak = np.maximum.accumulate(cum); mdd = float((peak - cum).max())
    return {
        "n":      len(trs),
        "wr":     wins / len(p),
        "pnl":    float(p.sum()),
        "sharpe": sharpe,
        "mdd":    mdd,
        "annual": float(p.sum()) / days * 365,
    }


def main():
    print(f"{'asset':>6} {'rsi_lo/hi':>10} {'TP':>5} {'SL':>5} {'hold':>5} "
          f"{'lev':>4} {'trades':>7} {'WR':>7} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'annual$':>9}")
    print("-" * 100)
    for asset, path in [("ETH", "data/btc/ohlcv_eth_1h.jsonl"),
                         ("SOL", "data/btc/ohlcv_sol_1h.jsonl")]:
        candles = load_candles(Path(path))
        if not candles:
            continue
        closes = np.asarray([float(c["close"]) for c in candles])
        rsi14 = rsi_fn(closes, 14)
        n = len(candles); ts = int(n * 0.70)
        days = (n - ts) / 24
        for lo, hi in [(20, 80), (25, 75), (30, 70)]:
            for tp, sl_ in [(0.010, 0.020), (0.012, 0.025), (0.015, 0.030)]:
                for max_h in (8, 16):
                    for lev in (5, 10, 15):
                        trs = simulate(candles, rsi14, rsi_low=lo, rsi_high=hi,
                                       tp_pct=tp, sl_pct=sl_, max_hold=max_h,
                                       leverage=lev, stake_usd=100.0, test_start_idx=ts)
                        s = agg(trs, days)
                        if not s or s["n"] < 30 or s["pnl"] < 5 or s["sharpe"] < 2:
                            continue
                        print(f"{asset:>6} {lo}/{hi:<7d} {tp*100:>4.2f}% {sl_*100:>4.2f}% {max_h:>4}h "
                              f"{lev:>3}x {s['n']:>7d} {s['wr']*100:>6.1f}% "
                              f"{s['pnl']:>+10.2f} {s['sharpe']:>7.2f} {s['mdd']:>8.2f} "
                              f"${s['annual']:>+8.0f}")


if __name__ == "__main__":
    main()
