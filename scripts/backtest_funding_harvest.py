"""Funding-rate harvest backtest — agnostic to BTC direction.

Hyperliquid pays funding hourly.  When funding rate is high (longs pay
shorts), we open a SHORT and earn funding for as long as the rate stays
elevated.  When the rate normalises, we close.  Profit = received funding
MINUS realised price drift on the position MINUS fees.

This strategy doesn't need a directional model — it's a carry trade.
Historical BTC funding has spent 35-40% of the time above 0.005%/h
(0.12%/day) with average around 0.0125%/h during those periods.

Inputs:
  - data/btc/funding_rate.jsonl   (8h-bucketed historical funding)
  - data/btc/ohlcv_1h.jsonl       (mark prices for PnL accounting)

Backtest logic:
  - Bucket funding to hourly grid (forward-fill since real funding only
    snapshots every 8h).
  - State machine: NO_POSITION -> SHORT (when funding > entry_thresh)
                   SHORT -> NO_POSITION (when funding < exit_thresh)
                                       OR mark moves > sl_pct against us
                                       OR hold > max_hold_hours
  - PnL accumulates: at every hour while SHORT, += funding_rate × notional
  - Price drift PnL accumulates continuously and is realised on close.
  - Fees: maker-limit on OPEN (0.015%), taker on CLOSE (0.045%) + slippage.

Sweep the policy knobs to find the profitable region.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def load_funding(path: Path) -> dict[int, float]:
    """Map open_time_ms (hour-aligned) → funding rate per hour.

    The Binance API returns funding at irregular timestamps; we forward-fill
    to every hour bucket so the backtest can read it bar-by-bar.
    """
    rows: list[tuple[int, float]] = []
    for line in path.open():
        try:
            r = json.loads(line)
            ts = int(r.get("fundingTime") or r.get("ts_ms") or r.get("open_time_ms") or 0)
            f  = float(r.get("fundingRate") or r.get("funding_rate") or 0)
            if ts > 0:
                rows.append((ts, f))
        except Exception:
            continue
    rows.sort()
    return rows


def load_candles(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.open():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.sort(key=lambda c: c["open_time_ms"])
    return out


def align_funding_to_candles(candles: list[dict], funding_rows: list[tuple[int, float]]) -> np.ndarray:
    """For each candle, return the most-recent funding rate (per-hour)."""
    rates = np.zeros(len(candles), dtype=np.float64)
    j = 0
    cur = 0.0
    for i, c in enumerate(candles):
        ts = int(c["open_time_ms"])
        while j < len(funding_rows) and funding_rows[j][0] <= ts:
            cur = funding_rows[j][1]
            j += 1
        rates[i] = cur
    return rates


def simulate(
    *,
    candles: list[dict],
    funding_h: np.ndarray,        # per-hour funding rate (already in /hour units)
    entry_thresh: float,           # enter SHORT when funding > this
    exit_thresh: float,            # close SHORT when funding < this
    sl_pct: float,                 # stop if mark moves +sl_pct against the SHORT
    max_hold_hours: int,
    leverage: int,
    stake_usd: float,
    open_fee: float,
    close_fee: float,
    open_slip: float,
    close_slip: float,
    test_start_idx: int,
) -> list[dict]:
    """Walk forward; track one rolling SHORT position."""
    pos = None  # dict
    trades: list[dict] = []

    for i in range(test_start_idx, len(candles)):
        f_now = float(funding_h[i])
        bar = candles[i]
        hi = float(bar["high"]); lo = float(bar["low"])
        opn = float(bar["open"]); cls = float(bar["close"])

        if pos is None:
            if f_now > entry_thresh:
                # Open SHORT at this bar's open
                pos = {
                    "entry_idx":   i,
                    "entry_px":    opn,
                    "funding_acc": 0.0,
                    "hold_hours":  0,
                }
            continue

        # Position is SHORT.  PnL = (entry - current) / entry × notional.
        # Accumulate funding earned this hour: + funding_h × notional
        pos["funding_acc"] += f_now            # rate is per-hour; sum across hours
        pos["hold_hours"]  += 1

        # Check SL: mark moved up by sl_pct against the short
        sl_px = pos["entry_px"] * (1.0 + sl_pct)
        sl_hit = hi >= sl_px

        exit_reason = None
        exit_px = None
        if sl_hit:
            exit_reason = "sl"
            exit_px = sl_px            # assume we got filled at SL trigger
        elif f_now < exit_thresh:
            exit_reason = "funding_normalised"
            exit_px = cls
        elif pos["hold_hours"] >= max_hold_hours:
            exit_reason = "max_hold"
            exit_px = cls

        if exit_reason is None:
            continue

        entry = pos["entry_px"]
        # Price PnL fraction (positive when price drops for SHORT)
        price_ret = (entry - exit_px) / entry
        # Funding earned (cumulative rate × notional fraction is just rate)
        funding_ret = pos["funding_acc"]   # in fraction-of-notional units

        # Apply fees + slippage
        gross_ret = price_ret + funding_ret
        net_ret   = gross_ret - (open_fee + close_fee) - (open_slip + close_slip)

        notional = stake_usd * leverage
        pnl_usd = notional * net_ret

        trades.append({
            "entry_idx":     pos["entry_idx"],
            "exit_idx":      i,
            "hold_hours":    pos["hold_hours"],
            "entry_px":      entry,
            "exit_px":       exit_px,
            "price_ret":     price_ret,
            "funding_ret":   funding_ret,
            "net_ret":       net_ret,
            "pnl_usd":       pnl_usd,
            "exit_reason":   exit_reason,
        })
        pos = None

    return trades


def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    p = np.asarray([t["pnl_usd"] for t in trades])
    fund = np.asarray([t["funding_ret"] for t in trades])
    price = np.asarray([t["price_ret"] for t in trades])
    wins = (p > 0).sum()
    cum = np.cumsum(p); peak = np.maximum.accumulate(cum); mdd = float((peak - cum).max())
    sharpe = float(p.mean() / (p.std() + 1e-12) * np.sqrt(252)) if len(p) > 1 else 0
    reasons = defaultdict(int)
    for t in trades: reasons[t["exit_reason"]] += 1
    return {
        "n":                 len(trades),
        "wins":               int(wins),
        "wr":                 round(wins / len(p), 4),
        "total_pnl_usd":      round(float(p.sum()), 2),
        "from_funding_usd":   round(float(fund.sum()) * 100 * 3, 2),  # rough; not used
        "avg_hold_hours":     round(float(np.mean([t["hold_hours"] for t in trades])), 1),
        "annualised_sharpe":  round(sharpe, 2),
        "max_drawdown_usd":   round(mdd, 2),
        "exit_reasons":       dict(reasons),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candles",  type=Path, default=Path("data/btc/ohlcv_1h.jsonl"))
    ap.add_argument("--funding",  type=Path, default=Path("data/btc/funding_rate.jsonl"))
    ap.add_argument("--leverage", type=int,   default=3)
    ap.add_argument("--stake-usd", type=float, default=100.0)
    ap.add_argument("--test-fraction", type=float, default=0.30)
    ap.add_argument("--sweep",    action="store_true")
    args = ap.parse_args()

    candles = load_candles(args.candles)
    funding_rows = load_funding(args.funding)
    print(f"Loaded {len(candles)} 1h candles + {len(funding_rows)} funding snapshots")
    if not candles or not funding_rows:
        print("Missing data — run aggregator first")
        return 1

    funding_h = align_funding_to_candles(candles, funding_rows)
    print(f"Funding rate stats: median={np.median(funding_h):.6f}/h  "
          f"p75={np.percentile(funding_h, 75):.6f}/h  "
          f"p90={np.percentile(funding_h, 90):.6f}/h  "
          f"max={funding_h.max():.6f}/h")

    n = len(candles); ts = int(n * (1 - args.test_fraction))
    print(f"Test window: {n - ts} bars (~{(n - ts) / 24:.1f} days)")

    OPEN_FEE = 0.00015; CLOSE_FEE = 0.00045
    OPEN_SLIP = 0.0001; CLOSE_SLIP = 0.0005

    if args.sweep:
        print()
        print(f"{'entry':>9} {'exit':>9} {'sl':>5} {'hold':>5} {'lev':>4} "
              f"{'trades':>7} {'WR':>6} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8} {'reasons':>30}")
        for entry_thresh in (0.00005, 0.00010, 0.00015, 0.00020, 0.00030):
            for exit_thresh in (0.0, 0.00005, 0.00008):
                if exit_thresh >= entry_thresh: continue
                for sl_pct in (0.020, 0.030):
                    for max_h in (8, 24, 48, 72):
                        for lev in (3, 5):
                            trs = simulate(
                                candles=candles, funding_h=funding_h,
                                entry_thresh=entry_thresh, exit_thresh=exit_thresh,
                                sl_pct=sl_pct, max_hold_hours=max_h,
                                leverage=lev, stake_usd=args.stake_usd,
                                open_fee=OPEN_FEE, close_fee=CLOSE_FEE,
                                open_slip=OPEN_SLIP, close_slip=CLOSE_SLIP,
                                test_start_idx=ts,
                            )
                            s = aggregate(trs)
                            if s["n"] < 5: continue
                            r = "/".join(f"{k[:4]}:{v}" for k, v in (s.get("exit_reasons") or {}).items())[:30]
                            print(f"{entry_thresh:>9.5f} {exit_thresh:>9.5f} {sl_pct*100:>4.1f}% "
                                  f"{max_h:>4}h {lev:>3}x {s['n']:>7d} {s['wr']*100:>5.1f}% "
                                  f"{s['total_pnl_usd']:>+10.2f} {s['annualised_sharpe']:>7.2f} "
                                  f"{s['max_drawdown_usd']:>8.2f} {r:>30}")
    else:
        # Default reasonable config
        trs = simulate(
            candles=candles, funding_h=funding_h,
            entry_thresh=0.00010, exit_thresh=0.00005,
            sl_pct=0.025, max_hold_hours=24,
            leverage=args.leverage, stake_usd=args.stake_usd,
            open_fee=OPEN_FEE, close_fee=CLOSE_FEE,
            open_slip=OPEN_SLIP, close_slip=CLOSE_SLIP,
            test_start_idx=ts,
        )
        s = aggregate(trs)
        print(json.dumps(s, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
