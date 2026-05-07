"""Mean-reversion strategy backtest — pure technical, no ML.

Hypothesis: BTC 1h price reverts from RSI extremes within a few hours.
  - RSI(14, 1h) >= 70 → SHORT (fade overbought)
  - RSI(14, 1h) <= 30 → LONG  (buy oversold)
  - TP at 0.4-0.6%, SL at 1.0-1.5% (asymmetric: give time for revert)

Compute RSI in-place, walk forward, simulate exits with realistic
maker-on-open / taker-on-SL fees.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_candles(path: Path) -> list[dict]:
    out = []
    for line in path.open():
        try: out.append(json.loads(line))
        except Exception: continue
    out.sort(key=lambda c: c["open_time_ms"])
    return out


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI on a numpy array, returns same-length output (NaN for warmup)."""
    diffs = np.diff(closes, prepend=closes[0])
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    out = np.full(len(closes), np.nan)
    if len(closes) <= period:
        return out
    avg_gain = gains[1:period+1].mean()
    avg_loss = losses[1:period+1].mean()
    for i in range(period+1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / (avg_loss + 1e-12)
        out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def simulate(
    *,
    candles: list[dict],
    rsi_arr: np.ndarray,
    rsi_low: float,
    rsi_high: float,
    tp_pct: float,
    sl_pct: float,
    max_hold_hours: int,
    leverage: int,
    stake_usd: float,
    test_start_idx: int,
) -> list[dict]:
    OPEN_FEE = 0.00015; OPEN_SLIP = 0.0001
    TP_FEE   = 0.00015; TP_SLIP   = 0.0
    TAKER_FEE = 0.00045; TAKER_SLIP = 0.0005

    trades: list[dict] = []
    pos = None
    n = len(candles)
    for i in range(test_start_idx, n - 1):
        bar = candles[i]
        next_bar = candles[i + 1]
        hi = float(bar["high"]); lo = float(bar["low"])

        if pos is not None:
            entry = pos["entry_px"]; is_long = pos["is_long"]
            tp_px = entry * (1.0 + tp_pct) if is_long else entry * (1.0 - tp_pct)
            sl_px = entry * (1.0 - sl_pct) if is_long else entry * (1.0 + sl_pct)
            tp_hit = hi >= tp_px if is_long else lo <= tp_px
            sl_hit = lo <= sl_px if is_long else hi >= sl_px
            exit_reason = None; exit_px = None
            if tp_hit and sl_hit:
                exit_px, exit_reason = sl_px, "sl_pessimistic"
            elif tp_hit:
                exit_px, exit_reason = tp_px, "tp"
            elif sl_hit:
                exit_px, exit_reason = sl_px, "sl"
            else:
                pos["hold"] += 1
                if pos["hold"] >= max_hold_hours:
                    exit_px, exit_reason = float(bar["close"]), "max_hold"
            if exit_reason:
                ret = (exit_px - entry) / entry if is_long else (entry - exit_px) / entry
                ret -= OPEN_FEE + OPEN_SLIP
                if exit_reason == "tp":
                    ret -= TP_FEE + TP_SLIP
                else:
                    ret -= TAKER_FEE + TAKER_SLIP
                pnl = stake_usd * leverage * ret
                trades.append({
                    "entry_idx":   pos["entry_idx"], "exit_idx": i,
                    "is_long":     is_long, "entry_px": entry, "exit_px": exit_px,
                    "ret":         ret, "pnl_usd": pnl,
                    "hold_hours":  pos["hold"], "exit_reason": exit_reason,
                })
                pos = None

        if pos is None and not np.isnan(rsi_arr[i]):
            r = rsi_arr[i]
            is_long = None
            if r <= rsi_low:    is_long = True
            elif r >= rsi_high: is_long = False
            if is_long is not None:
                pos = {
                    "entry_idx": i + 1, "entry_px": float(next_bar["open"]),
                    "is_long":   is_long, "hold": 0,
                }
    return trades


def aggregate(trades: list[dict]) -> dict:
    if not trades: return {"n": 0}
    p = np.asarray([t["pnl_usd"] for t in trades])
    wins = (p > 0).sum()
    cum = np.cumsum(p); peak = np.maximum.accumulate(cum); mdd = float((peak - cum).max())
    sharpe = float(p.mean() / (p.std() + 1e-12) * np.sqrt(252 * 24)) if len(p) > 1 else 0
    reasons = defaultdict(int)
    for t in trades: reasons[t["exit_reason"]] += 1
    return {
        "n":                len(trades),
        "wins":              int(wins),
        "wr":                round(wins / len(p), 4),
        "total_pnl_usd":     round(float(p.sum()), 2),
        "annualised_sharpe": round(sharpe, 2),
        "max_drawdown_usd":  round(mdd, 2),
        "avg_hold_hours":    round(float(np.mean([t["hold_hours"] for t in trades])), 1),
        "exit_reasons":      dict(reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles", type=Path, default=Path("data/btc/ohlcv_1h.jsonl"))
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument("--stake-usd", type=float, default=100.0)
    ap.add_argument("--test-fraction", type=float, default=0.30)
    args = ap.parse_args()

    candles = load_candles(args.candles)
    closes = np.asarray([float(c["close"]) for c in candles])
    rsi14 = rsi(closes, 14)
    n = len(candles); ts = int(n * (1 - args.test_fraction))

    print(f"{'rsi_lo/hi':>10} {'TP':>5} {'SL':>5} {'hold':>5} {'lev':>4} "
          f"{'trades':>7} {'WR':>6} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'reasons':>25}")
    for lo, hi in [(25, 75), (30, 70), (35, 65), (20, 80)]:
        for tp, sl in [(0.004, 0.010), (0.006, 0.012), (0.008, 0.015), (0.010, 0.020)]:
            for max_h in (4, 8, 16, 24):
                for lev in (3, 5):
                    trs = simulate(
                        candles=candles, rsi_arr=rsi14,
                        rsi_low=lo, rsi_high=hi,
                        tp_pct=tp, sl_pct=sl,
                        max_hold_hours=max_h,
                        leverage=lev, stake_usd=args.stake_usd,
                        test_start_idx=ts,
                    )
                    s = aggregate(trs)
                    if s["n"] < 10: continue
                    r = "/".join(f"{k[:3]}:{v}" for k, v in (s.get("exit_reasons") or {}).items())[:25]
                    print(f"{lo}/{hi:<7d} {tp*100:>4.2f}% {sl*100:>4.2f}% {max_h:>4}h "
                          f"{lev:>3}x {s['n']:>7d} {s['wr']*100:>5.1f}% "
                          f"{s['total_pnl_usd']:>+10.2f} {s['annualised_sharpe']:>7.2f} "
                          f"{s['max_drawdown_usd']:>8.2f} {r:>25}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
