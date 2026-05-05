"""Regime gate: empirically-derived filter that vetoes prediction trades
when conditions match historical loss patterns.

Discovered from analysis of 161 historical paper trades:
- Trend-following with strong recent momentum loses 70%+ of the time
  (BTC 15m markets mean-revert; chasing the move = late entry).
- Medium-low confidence trades (fill_price in [0.45, 0.48)) are
  dominated by noise and lose 67% of the time.

When the gate triggers, the prediction is pushed back toward 0.5
(no edge) so the downstream risk agent will skip the trade. The
trade still appears in audit logs as filtered, not as skipped silently.

Configuration is a passive object — gate logic is pure function.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RegimeGateConfig:
    """Knobs for the regime gate. Defaults derived from G4 in backtest."""

    enabled: bool = False

    # Skip trades aligned with strong recent momentum (trend-following).
    # |prev_return_3c| above this threshold flags a "strong move".
    trend_follow_block_threshold: float = 0.003

    # Skip trades where the fill price (= our entry) is in the
    # low-edge / high-noise zone identified empirically.
    fill_price_skip_min: float = 0.45
    fill_price_skip_max: float = 0.48

    # Optional aggressive filters (G5 in backtest). Off by default
    # because they reduce sample size more than empirical safety justifies.
    skip_extreme_rsi: bool = False
    rsi_low: float = 0.30
    rsi_high: float = 0.70

    skip_bad_hours_utc: bool = False
    bad_hours_utc: tuple[int, ...] = (0, 1, 2, 3, 21)


@dataclass(frozen=True)
class RegimeGateDecision:
    allowed: bool
    reason: str  # short tag, e.g. "ok" or "trend_following_strong_momentum"


def _normalised_rsi_to_pct(rsi_norm: float) -> float:
    """Live features store RSI as ``(rsi_raw - 0.5) * 2`` clamped to [-1,1].
    Convert back to raw [0, 1] then to [0, 100] percent for thresholding.
    """
    raw = (rsi_norm / 2.0) + 0.5
    return max(0.0, min(1.0, raw)) * 100.0


def evaluate_regime_gate(
    *,
    config: RegimeGateConfig,
    fair_yes_prob: float,
    candidate_side: str,
    yes_price: float,
    features: Mapping[str, float],
    decision_hour_utc: int | None = None,
) -> RegimeGateDecision:
    """Decide whether the trade should be allowed.

    Args:
        config: gate parameters.
        fair_yes_prob: model output [0, 1].
        candidate_side: 'YES' or 'NO' (the side we'd take).
        yes_price: current YES price on Polymarket [0, 1].
        features: live feature dict keyed by f_btc_* names.
        decision_hour_utc: hour at decision time, optional.
    """
    if not config.enabled:
        return RegimeGateDecision(True, "gate_disabled")

    # The "fill price" we'd pay for the side we want
    side = (candidate_side or "").upper()
    if side == "YES":
        fill_price = yes_price
    elif side == "NO":
        fill_price = 1.0 - yes_price
    else:
        return RegimeGateDecision(True, "unknown_side_passthrough")

    # ── Rule 1: trend-following with strong recent momentum ─────────
    # Use prev_return_3c if present (preferred), else return_3c.
    ret_3c = features.get("f_btc_prev_return_3c")
    if ret_3c is None:
        ret_3c = features.get("f_btc_return_3c", 0.0)
    if ret_3c is None:
        ret_3c = 0.0

    if abs(ret_3c) > config.trend_follow_block_threshold:
        trend_up = ret_3c > 0
        betting_up = side == "YES"
        if trend_up == betting_up:
            return RegimeGateDecision(False, "trend_following_strong_momentum")

    # ── Rule 2: low-edge / noisy fill price zone ────────────────────
    if config.fill_price_skip_min <= fill_price < config.fill_price_skip_max:
        return RegimeGateDecision(False, "fill_price_in_noise_zone")

    # ── Rule 3 (optional): extreme RSI ───────────────────────────────
    if config.skip_extreme_rsi:
        rsi_norm = features.get("f_btc_rsi_14", 0.0)
        rsi_pct = _normalised_rsi_to_pct(rsi_norm)
        if rsi_pct < config.rsi_low * 100 or rsi_pct > config.rsi_high * 100:
            return RegimeGateDecision(False, "extreme_rsi")

    # ── Rule 4 (optional): unfavourable hour of day ──────────────────
    if config.skip_bad_hours_utc and decision_hour_utc is not None:
        if decision_hour_utc in config.bad_hours_utc:
            return RegimeGateDecision(False, "bad_hour_of_day")

    return RegimeGateDecision(True, "ok")
