from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from prediction_market_bot.app.settings import PredictionSettings, RiskSettings
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, RiskDecision


class RiskAgent:
    name = "risk-agent"

    def __init__(
        self,
        risk_settings: RiskSettings,
        prediction_settings: PredictionSettings,
        *,
        execution_mode: str = "",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.risk = risk_settings
        self.prediction = prediction_settings
        self._execution_mode = execution_mode
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._global_circuit_breaker = bool(risk_settings.global_circuit_breaker)
        self._manual_pause = bool(risk_settings.manual_pause)

    def set_global_circuit_breaker(self, enabled: bool) -> None:
        self._global_circuit_breaker = bool(enabled)

    def set_manual_pause(self, enabled: bool) -> None:
        self._manual_pause = bool(enabled)

    def run(
        self,
        prediction: PredictionResult,
        candidate: MarketCandidate | None = None,
        portfolio: object | None = None,
    ) -> RiskDecision:
        rationale: list[str] = []
        block_reasons: list[str] = []
        cap_notes: list[str] = []

        if self._manual_pause:
            block_reasons.append("manual_pause_active")
        if self._global_circuit_breaker:
            block_reasons.append("global_circuit_breaker_active")

        edge_bps = prediction.edge * 10_000.0
        if prediction.confidence < self.prediction.min_confidence:
            block_reasons.append(
                f"confidence_below_threshold confidence={prediction.confidence:.4f} min={self.prediction.min_confidence:.4f}"
            )
        if edge_bps < self.prediction.min_edge_bps:
            block_reasons.append(f"edge_below_threshold edge_bps={edge_bps:.2f} min={self.prediction.min_edge_bps}")

        if candidate is not None:
            liquidity = candidate.market.liquidity_usd
            spread_bps = candidate.market.spread_bps
            snapshot_age_sec = max((self.now_fn() - candidate.market.updated_at).total_seconds(), 0.0)
            if liquidity < self.risk.min_liquidity_usd:
                block_reasons.append(
                    f"liquidity_below_threshold liquidity_usd={liquidity:.2f} min={self.risk.min_liquidity_usd:.2f}"
                )
            if spread_bps > self.risk.max_spread_bps:
                block_reasons.append(f"spread_above_threshold spread_bps={spread_bps} max={self.risk.max_spread_bps}")
            if snapshot_age_sec > self.risk.max_snapshot_age_sec:
                block_reasons.append(
                    f"stale_market_data age_sec={snapshot_age_sec:.0f} max_age_sec={self.risk.max_snapshot_age_sec}"
                )
            rationale.extend(
                [
                    f"market_liquidity_usd={liquidity:.2f}",
                    f"market_spread_bps={spread_bps}",
                    f"snapshot_age_sec={snapshot_age_sec:.2f}",
                ]
            )
        else:
            rationale.append("market_guardrails=skipped_missing_candidate")

        raw_kelly = self._kelly_fraction(
            fair_prob=prediction.selected_fair_price,
            market_price=prediction.selected_market_price,
        )
        fractional_kelly = max(raw_kelly * self.risk.fractional_kelly, 0.0)
        proposed_fraction = max(fractional_kelly, 0.0)

        cap_position = max(self.risk.effective_max_position_pct(self._execution_mode), 0.0)
        cap_event = max(self.risk.max_event_bucket_pct, 0.0)
        cap_category = max(self.risk.max_category_bucket_pct, 0.0)
        cap_portfolio = max(self.risk.effective_max_portfolio_exposure_pct(self._execution_mode), 0.0)
        cap_per_market = max(self.risk.max_per_market_exposure_pct, 0.0)
        applied_cap = min(cap_position, cap_event, cap_category, cap_portfolio, cap_per_market)
        if applied_cap <= 0.0:
            block_reasons.append("exposure_cap_reached applied_cap=0")
        bankroll_fraction = min(proposed_fraction, applied_cap)
        effective_bankroll = self.risk.effective_bankroll_usd(self._execution_mode)
        stake_usd = bankroll_fraction * effective_bankroll

        if stake_usd < self.risk.min_bet_usd:
            block_reasons.append(f"stake_below_min_bet stake_usd={stake_usd:.2f} min_bet_usd={self.risk.min_bet_usd:.2f}")
        if proposed_fraction > cap_position:
            cap_notes.append("position_exposure_cap")
        if proposed_fraction > cap_event:
            cap_notes.append("event_exposure_cap")
        if proposed_fraction > cap_category:
            cap_notes.append("category_exposure_cap")
        if proposed_fraction > cap_portfolio:
            cap_notes.append("portfolio_exposure_cap")
        if proposed_fraction > cap_per_market:
            cap_notes.append("per_market_exposure_cap")

        portfolio_exposure_usd = 0.0
        market_exposure_usd = 0.0
        realized_pnl_usd = 0.0
        if portfolio is not None:
            portfolio_exposure_usd = max(self._to_float(getattr(portfolio, "total_exposure_usd", 0.0)), 0.0)
            realized_pnl_usd = self._to_float(getattr(portfolio, "realized_pnl_usd", 0.0))
            market_exposure = getattr(portfolio, "market_exposure_usd", {})
            if isinstance(market_exposure, Mapping):
                market_exposure_usd = max(self._to_float(market_exposure.get(prediction.market_id, 0.0)), 0.0)
        else:
            rationale.append("portfolio_guardrails=skipped_missing_portfolio")

        portfolio_cap_usd = max(self.risk.effective_max_portfolio_exposure_pct(self._execution_mode), 0.0) * effective_bankroll
        market_cap_usd = max(self.risk.max_per_market_exposure_pct, 0.0) * effective_bankroll
        if portfolio_exposure_usd + stake_usd > portfolio_cap_usd + 1e-9:
            block_reasons.append(
                "portfolio_exposure_cap_reached "
                f"current={portfolio_exposure_usd:.2f} proposed={stake_usd:.2f} cap={portfolio_cap_usd:.2f}"
            )
        if market_exposure_usd + stake_usd > market_cap_usd + 1e-9:
            block_reasons.append(
                "market_exposure_cap_reached "
                f"market={prediction.market_id} current={market_exposure_usd:.2f} proposed={stake_usd:.2f} cap={market_cap_usd:.2f}"
            )

        daily_stop_limit_usd = max(self.risk.daily_stop_loss_pct, 0.0) * effective_bankroll
        if daily_stop_limit_usd > 0.0 and realized_pnl_usd <= (-daily_stop_limit_usd):
            block_reasons.append(
                "daily_stop_triggered "
                f"realized_pnl_usd={realized_pnl_usd:.2f} limit={-daily_stop_limit_usd:.2f}"
            )
            self._global_circuit_breaker = True
            block_reasons.append("global_circuit_breaker_trip_daily_stop")

        approved = len(block_reasons) == 0
        rationale.extend(
            [
                f"edge_bps={edge_bps:.2f}",
                f"confidence={prediction.confidence:.4f}",
                f"raw_kelly={raw_kelly:.6f}",
                f"fractional_kelly={fractional_kelly:.6f}",
                f"proposed_fraction={proposed_fraction:.6f}",
                f"cap_position={cap_position:.6f}",
                f"cap_event={cap_event:.6f}",
                f"cap_category={cap_category:.6f}",
                f"cap_portfolio={cap_portfolio:.6f}",
                f"cap_per_market={cap_per_market:.6f}",
                f"applied_cap={applied_cap:.6f}",
                f"final_fraction={bankroll_fraction:.6f}",
                f"portfolio_exposure_usd={portfolio_exposure_usd:.2f}",
                f"market_exposure_usd={market_exposure_usd:.2f}",
                f"portfolio_cap_usd={portfolio_cap_usd:.2f}",
                f"market_cap_usd={market_cap_usd:.2f}",
                f"realized_pnl_usd={realized_pnl_usd:.2f}",
                f"daily_stop_limit_usd={daily_stop_limit_usd:.2f}",
                f"manual_pause={self._manual_pause}",
                f"global_circuit_breaker={self._global_circuit_breaker}",
            ]
        )
        if approved:
            rationale.append("approved")
        else:
            if not block_reasons:
                block_reasons.append("rejected_without_reason")
            rationale.extend(block_reasons)
        rationale.extend(cap_notes)

        approved_fraction = bankroll_fraction if approved else 0.0
        approved_stake = stake_usd if approved else 0.0

        return RiskDecision(
            market_id=prediction.market_id,
            approved=approved,
            side=prediction.selected_side,
            stake_usd=round(approved_stake, 2),
            bankroll_fraction=round(approved_fraction, 6),
            fractional_kelly=round(fractional_kelly, 6),
            max_loss_usd=round(approved_stake, 2),
            reasoning=tuple(rationale),
        )

    @staticmethod
    def _kelly_fraction(fair_prob: float, market_price: float) -> float:
        # Binary share priced at c with payout 1 if correct:
        # f* = (p - c) / (1 - c), with denominator protection.
        denominator = max(1.0 - market_price, 1e-9)
        return (fair_prob - market_price) / denominator

    @staticmethod
    def _to_float(value: Any) -> float:
        if isinstance(value, bool):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if text:
                try:
                    return float(text)
                except ValueError:
                    return 0.0
        return 0.0
