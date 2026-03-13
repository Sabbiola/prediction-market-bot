from __future__ import annotations

from prediction_market_bot.domain.enums import ExecutionStatus, OutcomeSide
from prediction_market_bot.domain.models import ExecutionResult, OrderIntent, PredictionResult, RiskDecision
from prediction_market_bot.interfaces import TradeExecutor


class DryRunExecutor:
    def place_order(self, order: OrderIntent) -> ExecutionResult:
        if order.stake_usd <= 0.0:
            return ExecutionResult(
                market_id=order.market_id,
                status=ExecutionStatus.FAILED,
                side=order.side,
                stake_usd=0.0,
                order_id=None,
                message="order_rejected_invalid_stake",
            )

        slippage_bps = self._slippage_bps(order.market_id)
        fill_price = self._apply_slippage(order.limit_price, order.side, slippage_bps)
        order_id = self._build_order_id(order)
        return ExecutionResult(
            market_id=order.market_id,
            status=ExecutionStatus.FILLED,
            side=order.side,
            stake_usd=order.stake_usd,
            fill_price=fill_price,
            order_id=order_id,
            message=f"order_created_and_filled dry_run slippage_bps={slippage_bps}",
        )

    @staticmethod
    def _build_order_id(order: OrderIntent) -> str:
        stake_cents = int(round(order.stake_usd * 100))
        return f"dryrun-{order.market_id}-{order.side.value.lower()}-{stake_cents}"

    @staticmethod
    def _slippage_bps(market_id: str) -> int:
        # Deterministic per market_id, in range [0, 6] bps.
        return sum(ord(char) for char in market_id) % 7

    @staticmethod
    def _apply_slippage(limit_price: float, side: OutcomeSide, slippage_bps: int) -> float:
        del side  # Same deterministic slippage model for YES and NO in dry-run.
        slipped = limit_price + (slippage_bps / 10_000.0)
        return round(min(max(slipped, 0.01), 0.99), 4)


class ExecutionAgent:
    name = "execution-agent"

    def __init__(self, venue: str, executor: TradeExecutor | None = None) -> None:
        self.venue = venue
        self.executor = executor or DryRunExecutor()

    def build_order_intent(self, risk: RiskDecision, prediction: PredictionResult) -> OrderIntent:
        return OrderIntent(
            market_id=risk.market_id,
            venue=self.venue,
            side=risk.side,
            stake_usd=risk.stake_usd,
            limit_price=prediction.selected_market_price,
            rationale=" | ".join(prediction.rationale),
        )

    def run(self, risk: RiskDecision, prediction: PredictionResult) -> ExecutionResult:
        if not risk.approved:
            return ExecutionResult(
                market_id=risk.market_id,
                status=ExecutionStatus.SKIPPED,
                side=risk.side,
                stake_usd=0.0,
                message="trade_blocked_by_risk_agent",
            )

        order = self.build_order_intent(risk, prediction)
        return self.executor.place_order(order)
