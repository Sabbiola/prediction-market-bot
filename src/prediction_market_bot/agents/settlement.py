from __future__ import annotations

from prediction_market_bot.domain.enums import ExecutionStatus, OutcomeClassification, OutcomeSide
from prediction_market_bot.domain.models import ExecutionResult, SettlementResult


class SettlementAgent:
    name = "settlement-agent"

    def settle(self, execution: ExecutionResult, resolved_yes: bool) -> SettlementResult:
        classification, avg_price, pnl = self._simulate_settlement(execution=execution, resolved_yes=resolved_yes)
        return SettlementResult(
            market_id=execution.market_id,
            side=execution.side,
            stake_usd=execution.stake_usd,
            avg_price=avg_price,
            resolved_yes=resolved_yes,
            pnl_usd=round(pnl, 2),
            outcome_classification=classification,
        )

    @staticmethod
    def _simulate_settlement(
        *, execution: ExecutionResult, resolved_yes: bool
    ) -> tuple[OutcomeClassification, float, float]:
        if execution.status in {ExecutionStatus.SKIPPED, ExecutionStatus.FAILED}:
            return (OutcomeClassification.SKIPPED, 0.0, 0.0)
        if execution.fill_price is None or execution.stake_usd <= 0.0:
            return (OutcomeClassification.SKIPPED, 0.0, 0.0)

        won = (execution.side == OutcomeSide.YES and resolved_yes) or (
            execution.side == OutcomeSide.NO and not resolved_yes
        )
        if won:
            pnl = execution.stake_usd * ((1.0 - execution.fill_price) / max(execution.fill_price, 1e-9))
            classification = OutcomeClassification.WIN
        else:
            pnl = -execution.stake_usd
            classification = OutcomeClassification.LOSS

        if abs(pnl) < 1e-6:
            classification = OutcomeClassification.BREAKEVEN

        return (classification, execution.fill_price, pnl)
