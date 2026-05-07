"""RSI mean-reversion + ML filter prediction engine.

Backtest-derived strategy that beat HL frictions in our walk-forward
simulator (RSI 20/80 + v7 ML filter min 0.30 → Sharpe 12.51, WR 65.6%).

Logic:
  - Compute RSI(14) on the 1h close history.
  - If RSI <= rsi_low  → candidate LONG  (oversold, expect reversion up)
  - If RSI >= rsi_high → candidate SHORT (overbought, expect reversion down)
  - Otherwise: neutral (return prob 0.5, no trade).
  - For a candidate side, optionally check the v7 TP-classifier P(TP_hit
    | side); if available and below ml_min_prob, downgrade to neutral.
  - Output a calibrated yes-prob: 0.5 + 0.10 for LONG, 0.5 - 0.10 for SHORT,
    so the downstream risk + execution layers see a clear directional signal
    and edge above neutral.

The TP/SL/hold knobs are configured at the executor level (Setup B-style
via app_model_g_staging.yaml).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RsiMLConfig:
    rsi_low:        float = 25.0
    rsi_high:       float = 75.0
    ml_min_prob:    float = 0.30   # 0.0 disables the ML filter
    rsi_period:     int   = 14
    edge_strength:  float = 0.10   # how far from 0.5 we push the prob


def _rsi_wilders(closes: list[float] | np.ndarray, period: int = 14) -> float:
    """Latest Wilders RSI value, in [0, 100], or NaN if insufficient history."""
    closes = np.asarray(closes, dtype=np.float64)
    if len(closes) <= period:
        return float("nan")
    diffs = np.diff(closes)
    gains  = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_g  = gains[:period].mean()
    avg_l  = losses[:period].mean()
    for i in range(period, len(diffs)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    rs = avg_g / (avg_l + 1e-12)
    return 100.0 - 100.0 / (1.0 + rs)


def predict_rsi_ml(
    *,
    config: RsiMLConfig,
    hourly_closes: list[float] | np.ndarray | None = None,
    rsi_raw: float | None = None,
    p_long_ml:  float | None = None,
    p_short_ml: float | None = None,
) -> tuple[float, str, dict]:
    """Return (yes_prob, side, debug_dict).

    side = "YES" / "NO" / "NEUTRAL".
    yes_prob > 0.5 means LONG, < 0.5 means SHORT.  When neutral we return
    exactly 0.5 so the risk gate skips the trade.

    Two ways to feed RSI:
      - rsi_raw: pre-computed RSI in [0, 100] from the upstream feature
        enricher.  Preferred — guarantees parity with training.
      - hourly_closes: list of 1h close prices, RSI is computed here.
        Fallback used by tests / backtests.
    """
    if rsi_raw is not None:
        rsi_v = float(rsi_raw)
    else:
        if hourly_closes is None or len(hourly_closes) < config.rsi_period + 1:
            return 0.5, "NEUTRAL", {"reason": "insufficient_history"}
        rsi_v = _rsi_wilders(hourly_closes, config.rsi_period)

    if not np.isfinite(rsi_v):
        return 0.5, "NEUTRAL", {"reason": "rsi_nan"}

    cand_long  = rsi_v <= config.rsi_low
    cand_short = rsi_v >= config.rsi_high
    if not (cand_long or cand_short):
        return 0.5, "NEUTRAL", {"reason": "rsi_in_band", "rsi": rsi_v}

    # Apply ML filter if probabilities are provided
    if config.ml_min_prob > 0:
        if cand_long and (p_long_ml is None or p_long_ml < config.ml_min_prob):
            return 0.5, "NEUTRAL", {
                "reason": "ml_filter_blocked_long",
                "rsi":    rsi_v,
                "p_long": p_long_ml,
                "min":    config.ml_min_prob,
            }
        if cand_short and (p_short_ml is None or p_short_ml < config.ml_min_prob):
            return 0.5, "NEUTRAL", {
                "reason":  "ml_filter_blocked_short",
                "rsi":     rsi_v,
                "p_short": p_short_ml,
                "min":     config.ml_min_prob,
            }

    if cand_long:
        return 0.5 + config.edge_strength, "YES", {
            "reason": "oversold_long_entry", "rsi": rsi_v, "p_long_ml": p_long_ml,
        }
    return 0.5 - config.edge_strength, "NO", {
        "reason": "overbought_short_entry", "rsi": rsi_v, "p_short_ml": p_short_ml,
    }
