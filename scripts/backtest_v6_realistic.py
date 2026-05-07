"""Event-driven backtest for v6 1h CatBoost model on Hyperliquid perps.

Unlike btc_train_v2's PnL-Sharpe metric (a single bps move per trade), this
simulator walks forward bar-by-bar and applies the actual rules the live
bot uses:

  - For each 1h candle the bot would have a calibrated yes probability;
    if the edge over neutral exceeds `edge_thresh`, open a position.
  - Side: prob > 0.5 → LONG, prob < 0.5 → SHORT.
  - TP/SL atomic and reduce-only — if EITHER is breached inside any
    future candle's [low, high], the position closes at that price.
  - If both TP and SL are inside the same future candle, the simulator
    assumes SL hits first (conservative — the BTC perp orderbook would
    typically take whichever level the market crossed first; we have no
    intra-bar tick data to tell, so the pessimistic choice protects us
    from overstating PnL).
  - If neither hits within `time_exit_bars` (default 4 bars = 4 hours),
    the position is force-closed at the open of bar t + time_exit_bars.
  - Fees: taker round-trip (~0.045% × 2 = 0.09%) applied to NOTIONAL.
  - Slippage: 0.05% of mark on entry AND exit, conservative.
  - Funding: negligible at <8h hold, ignored. (Bot E hold = 4h max.)
  - Agreement-skip: when an open position already matches the new
    signal's direction, the new signal is skipped (matches Setup B
    executor logic).

Output:
  - per-trade ledger (entry, exit, hold_bars, exit_reason, gross/net pnl)
  - aggregate stats: total trades, win rate, Sharpe, MDD, EV/trade
  - threshold sensitivity table

Usage on the VPS:
  python scripts/backtest_v6_realistic.py \\
    --model data/models/v6/btc_1h_best.json \\
    --interval 1h --leverage 3 \\
    --tp-pct 0.020 --sl-pct 0.012 \\
    --time-exit-bars 4 \\
    --edge-thresh 0.005 \\
    --fees-pct 0.00045 --slippage-pct 0.0005
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Reuse btc_train_v2's data + feature builders so backtest features match the
# exact training distribution.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import btc_train_v2 as t  # type: ignore


# ── Feature pipeline (mirrors training) ─────────────────────────────────────

def _build_aligned_features(interval: str, lookback_months: int):
    """Return (X, candles, indices) where:
       - X[i] is the feature vector at candle index `indices[i]`,
       - candles is the full 1h OHLCV list (used by the trade simulator).

    Reuses load_data + build_dataset so feature engineering is bit-identical
    to training; the candles array carries (open_time_ms, open, high, low,
    close, volume) needed for the bar-by-bar simulator.
    """
    data = t.load_data(interval, lookback_months)
    if not data["binance"]:
        raise SystemExit("No binance data — run btc_aggregate_to_1h.py first")
    X, y = t.build_dataset(data["binance"], data["coinbase"], data["derivatives"])
    # build_dataset uses every completed candle from REQUIRED_LOOKBACK to len-1
    indices = list(range(t.REQUIRED_LOOKBACK, len(data["binance"]) - 1))
    assert len(indices) == len(X), (len(indices), len(X))
    return np.asarray(X, dtype=np.float64), data["binance"], indices, y


# ── Simulator ───────────────────────────────────────────────────────────────

def simulate(
    *,
    probs: np.ndarray,           # calibrated yes probabilities, aligned with candles[indices]
    candles: list,
    indices: list,
    leverage: int,
    tp_pct: float,
    sl_pct: float,
    time_exit_bars: int,
    edge_thresh: float,
    fees_pct: float,             # PER FILL (e.g. 0.00045 for HL taker 0.045%)
    slippage_pct: float,         # PER FILL
    stake_usd: float,
    agreement_skip: bool,
    test_start_idx: int = 0,
) -> dict:
    """Walks forward through `indices[test_start_idx:]`, opening trades and
    simulating their exit using future bar high/low/open."""
    trades: list[dict] = []
    equity_curve: list[float] = []
    bankroll = 0.0
    open_pos = None  # dict or None

    n = len(indices)
    for i in range(test_start_idx, n):
        candle_idx = indices[i]
        prob = float(probs[i])

        # Simulate any open position before considering a new entry
        if open_pos is not None:
            entry_px = open_pos["entry_px"]
            is_long = open_pos["is_long"]
            opened_at = open_pos["opened_at_idx"]
            hold = candle_idx - opened_at
            tp = entry_px * (1.0 + tp_pct) if is_long else entry_px * (1.0 - tp_pct)
            sl = entry_px * (1.0 - sl_pct) if is_long else entry_px * (1.0 + sl_pct)

            bar = candles[candle_idx]
            hi, lo = float(bar["high"]), float(bar["low"])

            tp_hit = (hi >= tp) if is_long else (lo <= tp)
            sl_hit = (lo <= sl) if is_long else (hi >= sl)

            exit_px = None
            exit_reason = None
            if tp_hit and sl_hit:
                exit_px, exit_reason = sl, "sl_conservative"  # pessimistic
            elif tp_hit:
                exit_px, exit_reason = tp, "tp"
            elif sl_hit:
                exit_px, exit_reason = sl, "sl"
            elif hold >= time_exit_bars:
                # Force-close at this bar's open
                exit_px, exit_reason = float(bar["open"]), "time_exit"

            if exit_px is not None:
                # Compute realised PnL (signed)
                ret_pct = (exit_px - entry_px) / entry_px if is_long else (entry_px - exit_px) / entry_px
                # Apply slippage on entry AND exit (each cost a fixed % of mark)
                ret_pct -= 2 * slippage_pct
                # Apply fees: per-fill on notional (open + close)
                ret_pct_after_fees = ret_pct - 2 * fees_pct
                notional = stake_usd * leverage
                pnl_usd = notional * ret_pct_after_fees
                bankroll += pnl_usd

                trades.append({
                    "open_idx":   opened_at,
                    "close_idx":  candle_idx,
                    "hold_bars":  hold,
                    "is_long":    is_long,
                    "entry_px":   entry_px,
                    "exit_px":    exit_px,
                    "ret_pct":    ret_pct,                  # net of slippage
                    "ret_after_fees": ret_pct_after_fees,
                    "pnl_usd":    pnl_usd,
                    "exit_reason": exit_reason,
                    "leverage":   leverage,
                })
                open_pos = None

        equity_curve.append(bankroll)

        # Decide whether to open a new position this bar
        if open_pos is not None:
            continue
        if abs(prob - 0.5) < edge_thresh:
            continue
        is_long = prob > 0.5

        # Agreement-skip: not relevant when no open position; only matters
        # when the same direction is already open (no pyramiding).  Kept for
        # parity with executor — when open_pos is None this branch never fires.
        if agreement_skip and open_pos and open_pos["is_long"] == is_long:
            continue

        # Open at the open of the next bar (executor latency is roughly
        # one candle in real life; we'd use the close of the signal bar
        # in real BG mode but most bots use next-open which is more honest).
        if candle_idx + 1 >= len(candles):
            break
        next_bar = candles[candle_idx + 1]
        entry_px = float(next_bar["open"])
        open_pos = {
            "is_long": is_long,
            "entry_px": entry_px,
            "opened_at_idx": candle_idx + 1,
        }

    # If a position is still open at the end of the data, do NOT mark-to-market
    # — leave it for honesty.
    return {
        "trades":         trades,
        "equity_curve":   equity_curve,
        "n_trades":       len(trades),
        "final_pnl":      bankroll,
        "open_at_end":    open_pos is not None,
    }


def aggregate(trades: list[dict], stake_usd: float) -> dict:
    if not trades:
        return {"n": 0}
    pnls = np.asarray([t["pnl_usd"] for t in trades])
    rets = np.asarray([t["ret_after_fees"] for t in trades])
    wins = pnls > 0
    losses = pnls < 0
    sharpe = float(rets.mean() / (rets.std() + 1e-12) * np.sqrt(252 * 24))  # annualised hourly
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    mdd = float((peak - cum).max())
    reasons = {}
    for tr in trades:
        reasons[tr["exit_reason"]] = reasons.get(tr["exit_reason"], 0) + 1
    return {
        "n":               len(trades),
        "win_rate":        round(float(wins.mean()), 4),
        "wins":            int(wins.sum()),
        "losses":          int(losses.sum()),
        "total_pnl_usd":   round(float(pnls.sum()), 2),
        "avg_pnl_usd":     round(float(pnls.mean()), 4),
        "median_pnl_usd":  round(float(np.median(pnls)), 4),
        "best_trade":      round(float(pnls.max()), 2),
        "worst_trade":     round(float(pnls.min()), 2),
        "annualised_sharpe": round(sharpe, 2),
        "max_drawdown_usd": round(mdd, 2),
        "exit_reasons":    reasons,
        "avg_hold_bars":   round(float(np.mean([t["hold_bars"] for t in trades])), 2),
        "stake_per_trade": stake_usd,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model",         default="data/models/v6/btc_1h_best.json")
    ap.add_argument("--interval",      default="1h")
    ap.add_argument("--lookback-months", type=int, default=36)
    ap.add_argument("--leverage",      type=int,   default=3)
    ap.add_argument("--tp-pct",        type=float, default=0.020)
    ap.add_argument("--sl-pct",        type=float, default=0.012)
    ap.add_argument("--time-exit-bars", type=int,  default=4)
    ap.add_argument("--edge-thresh",   type=float, default=0.005)
    ap.add_argument("--fees-pct",      type=float, default=0.00045)
    ap.add_argument("--slippage-pct",  type=float, default=0.0005)
    ap.add_argument("--stake-usd",     type=float, default=50.0)
    ap.add_argument("--agreement-skip", action="store_true", default=True)
    ap.add_argument("--test-fraction", type=float, default=0.15,
                    help="Last X fraction of bars for backtest")
    ap.add_argument("--sweep",         action="store_true",
                    help="Sweep across edge_thresh, tp_pct, sl_pct")
    args = ap.parse_args()

    print(f"Loading features and model artefact ...")
    X, candles, indices, _y = _build_aligned_features(args.interval, args.lookback_months)
    print(f"  {len(indices)} aligned bars (REQUIRED_LOOKBACK={t.REQUIRED_LOOKBACK})")

    # Load v6 calibrated probs
    artefact = json.loads(Path(args.model).read_text())
    cb_path = Path(args.model).with_name(Path(args.model).name.replace(".json", "_catboost.cbm"))
    if not cb_path.exists():
        cb_path = Path(args.model).parent / artefact.get("model_artifact_filename",
                                                           "btc_1h_best_catboost.cbm")
    print(f"  CatBoost: {cb_path.name}")

    from catboost import CatBoostClassifier
    cbm = CatBoostClassifier()
    cbm.load_model(str(cb_path))
    raw_probs = cbm.predict_proba(X)[:, 1]

    cal_method = artefact.get("calibration", {}).get("method", "none")
    cal_params = artefact.get("calibration", {}).get("parameters", {}) or {}
    if cal_method == "temperature":
        T = float(cal_params.get("T", 1.0))
        # Apply temperature scaling on the logit
        logit = np.log(np.clip(raw_probs, 1e-7, 1 - 1e-7) / np.clip(1 - raw_probs, 1e-7, 1))
        probs = 1.0 / (1.0 + np.exp(-logit / T))
        print(f"  Calibration: temperature T={T:.4f}")
    else:
        probs = raw_probs
        print(f"  Calibration: none ({cal_method!r})")

    # Test split: last test_fraction
    n = len(indices)
    test_start = int(n * (1 - args.test_fraction))
    print(f"  Backtest window: {n - test_start} bars (~{(n - test_start) / 24:.1f} days)")

    # Single run with the requested params
    res = simulate(
        probs=probs,
        candles=candles,
        indices=indices,
        leverage=args.leverage,
        tp_pct=args.tp_pct,
        sl_pct=args.sl_pct,
        time_exit_bars=args.time_exit_bars,
        edge_thresh=args.edge_thresh,
        fees_pct=args.fees_pct,
        slippage_pct=args.slippage_pct,
        stake_usd=args.stake_usd,
        agreement_skip=args.agreement_skip,
        test_start_idx=test_start,
    )
    stats = aggregate(res["trades"], args.stake_usd)

    print()
    print("=" * 70)
    print(f"REALISTIC BACKTEST — v6 1h CatBoost on HL perp")
    print("=" * 70)
    print(f"  Setup: leverage={args.leverage}x  TP={args.tp_pct*100:.2f}%  "
          f"SL={args.sl_pct*100:.2f}%  time_exit={args.time_exit_bars}h")
    print(f"  Frictions: fees={args.fees_pct*100:.3f}%/fill  "
          f"slippage={args.slippage_pct*100:.3f}%/fill  "
          f"edge_thresh={args.edge_thresh:.4f}")
    print(f"  Bankroll: ${args.stake_usd}/trade")
    print()
    print(f"  Trades placed:        {stats['n']}")
    if stats["n"] == 0:
        return 0
    print(f"  Win rate:             {stats['win_rate']*100:.1f}% "
          f"({stats['wins']} W / {stats['losses']} L)")
    print(f"  Total PnL (net):      ${stats['total_pnl_usd']:+,.2f}")
    print(f"  Avg PnL / trade:      ${stats['avg_pnl_usd']:+.4f}")
    print(f"  Best / Worst trade:   ${stats['best_trade']:+.2f}  /  ${stats['worst_trade']:+.2f}")
    print(f"  Annualised Sharpe:    {stats['annualised_sharpe']:.2f}")
    print(f"  Max drawdown:         ${stats['max_drawdown_usd']:.2f}")
    print(f"  Avg hold:             {stats['avg_hold_bars']:.2f} bars")
    print(f"  Exit reasons:         {stats['exit_reasons']}")

    # ── Optional sweep over key knobs ───────────────────────────────────
    if args.sweep:
        print()
        print("=" * 70)
        print("THRESHOLD / TP / SL SWEEP")
        print("=" * 70)
        print(f"{'edge':>6} {'tp':>6} {'sl':>6} {'trades':>7} {'WR':>7} {'pnl_$':>10} {'sharpe':>7} {'mdd_$':>8}")
        for edge in (0.002, 0.005, 0.010, 0.020, 0.030):
            for tp, sl in [(0.015, 0.010), (0.020, 0.012), (0.025, 0.015), (0.030, 0.018)]:
                r = simulate(
                    probs=probs, candles=candles, indices=indices,
                    leverage=args.leverage, tp_pct=tp, sl_pct=sl,
                    time_exit_bars=args.time_exit_bars,
                    edge_thresh=edge,
                    fees_pct=args.fees_pct, slippage_pct=args.slippage_pct,
                    stake_usd=args.stake_usd, agreement_skip=args.agreement_skip,
                    test_start_idx=test_start,
                )
                s = aggregate(r["trades"], args.stake_usd)
                if s["n"] > 0:
                    print(f"{edge:>6.3f} {tp*100:>5.2f}% {sl*100:>5.2f}% {s['n']:>7d} "
                          f"{s['win_rate']*100:>6.1f}% {s['total_pnl_usd']:>+10.2f} "
                          f"{s['annualised_sharpe']:>7.2f} {s['max_drawdown_usd']:>8.2f}")
                else:
                    print(f"{edge:>6.3f} {tp*100:>5.2f}% {sl*100:>5.2f}%   no trades")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
