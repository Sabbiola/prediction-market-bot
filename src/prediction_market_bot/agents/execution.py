from __future__ import annotations

from prediction_market_bot.agents.executors import (
    DryRunExecutor,
    LiveDisabledExecutor,
    PaperExecutor,
    ShadowSignExecutor,
    _intent_id,
)
from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, TxConfirmationStatus
from prediction_market_bot.domain.models import (
    ExecutionResult,
    OrderIntent,
    PredictionResult,
    RiskDecision,
    TransactionAttempt,
)
from prediction_market_bot.interfaces import TradeExecutor


class ExecutionAgent:
    name = "execution-agent"

    def __init__(
        self,
        venue: str,
        executor: TradeExecutor | None = None,
        *,
        execution_mode: ExecutionMode = ExecutionMode.PAPER,
        rehearsal_executor: TradeExecutor | None = None,
        rehearsal_mode: ExecutionMode | None = None,
    ) -> None:
        self.venue = venue
        self.execution_mode = execution_mode
        self.executor = executor or self._default_executor(execution_mode)
        self.rehearsal_mode = rehearsal_mode
        self.rehearsal_executor = rehearsal_executor
        self.last_attempts: tuple[TransactionAttempt, ...] = ()

    @staticmethod
    def _default_executor(mode: ExecutionMode) -> TradeExecutor:
        if mode == ExecutionMode.PAPER:
            return PaperExecutor()
        if mode == ExecutionMode.SHADOW_SIGN:
            return ShadowSignExecutor()
        return LiveDisabledExecutor()

    def build_order_intent(self, risk: RiskDecision, prediction: PredictionResult) -> OrderIntent:
        return OrderIntent(
            market_id=risk.market_id,
            venue=self.venue,
            side=risk.side,
            stake_usd=risk.stake_usd,
            limit_price=prediction.selected_market_price,
            rationale=" | ".join(prediction.rationale),
        )

    def run(
        self,
        risk: RiskDecision,
        prediction: PredictionResult,
        *,
        order_intent: OrderIntent | None = None,
    ) -> ExecutionResult:
        self.last_attempts = ()
        if not risk.approved:
            return ExecutionResult(
                market_id=risk.market_id,
                execution_mode=self.execution_mode,
                status=ExecutionStatus.SKIPPED,
                side=risk.side,
                stake_usd=0.0,
                run_id="",
                review_queue_id="",
                confirmation_status=TxConfirmationStatus.UNKNOWN,
                message="trade_blocked_by_risk_or_review_gate",
            )

        order = order_intent or self.build_order_intent(risk, prediction)
        primary_result = self.executor.place_order(order)
        attempts: list[TransactionAttempt] = [self._extract_attempt(order, primary_result, self.execution_mode, lane="main", source=self.executor)]

        if self.rehearsal_executor is not None and self.rehearsal_mode is not None:
            rehearsal_result = self.rehearsal_executor.place_order(order)
            attempts.append(
                self._extract_attempt(
                    order,
                    rehearsal_result,
                    self.rehearsal_mode,
                    lane="rehearsal",
                    source=self.rehearsal_executor,
                )
            )

        self.last_attempts = tuple(attempts)
        return primary_result

    @staticmethod
    def _extract_attempt(
        order: OrderIntent,
        result: ExecutionResult,
        mode: ExecutionMode,
        *,
        lane: str,
        source: object,
    ) -> TransactionAttempt:
        existing = getattr(source, "last_attempt", None)
        if isinstance(existing, TransactionAttempt):
            if existing.lane == lane and existing.execution_mode == mode:
                return existing
            return existing.model_copy(update={"lane": lane, "execution_mode": mode})
        return TransactionAttempt(
            market_id=order.market_id,
            execution_mode=mode,
            lane=lane,
            intent_id=_intent_id(order),
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=result.status,
            tx_hash=None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=result.submitted_at,
            confirmed_at=result.confirmed_at,
            confirmation_status=result.confirmation_status,
            message=result.message,
        )


# Re-export for backward compatibility
__all__ = [
    "DryRunExecutor",
    "ExecutionAgent",
    "LiveDisabledExecutor",
    "PaperExecutor",
    "ShadowSignExecutor",
]
