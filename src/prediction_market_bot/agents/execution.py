from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import (
    ExecutionResult,
    OrderIntent,
    PredictionResult,
    RiskDecision,
    TransactionAttempt,
)
from prediction_market_bot.interfaces import TradeExecutor


class PaperExecutor:
    def __init__(self) -> None:
        self.last_attempt: TransactionAttempt | None = None

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        intent_id = _intent_id(order)
        if order.stake_usd <= 0.0:
            result = ExecutionResult(
                market_id=order.market_id,
                execution_mode=ExecutionMode.PAPER,
                status=ExecutionStatus.FAILED,
                side=order.side,
                stake_usd=0.0,
                order_id=None,
                intent_id=intent_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                message="order_rejected_invalid_stake",
            )
            self.last_attempt = TransactionAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.PAPER,
                lane="paper",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=result.status,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                message=result.message,
            )
            return result

        slippage_bps = self._slippage_bps(order.market_id)
        fill_price = self._apply_slippage(order.limit_price, order.side, slippage_bps)
        order_id = self._build_order_id(order)
        result = ExecutionResult(
            market_id=order.market_id,
            execution_mode=ExecutionMode.PAPER,
            status=ExecutionStatus.FILLED,
            side=order.side,
            stake_usd=order.stake_usd,
            fill_price=fill_price,
            order_id=order_id,
            intent_id=intent_id,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            confirmation_status=TxConfirmationStatus.MINED,
            message=f"order_created_and_filled paper_mode slippage_bps={slippage_bps}",
        )
        self.last_attempt = TransactionAttempt(
            market_id=order.market_id,
            execution_mode=ExecutionMode.PAPER,
            lane="paper",
            intent_id=intent_id,
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=result.status,
            tx_hash=None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            confirmation_status=TxConfirmationStatus.MINED,
            message=result.message,
            metadata=(f"fill_price={fill_price}", f"order_id={order_id}"),
        )
        return result

    @staticmethod
    def _build_order_id(order: OrderIntent) -> str:
        stake_cents = int(round(order.stake_usd * 100))
        return f"paper-{order.market_id}-{order.side.value.lower()}-{stake_cents}"

    @staticmethod
    def _slippage_bps(market_id: str) -> int:
        # Deterministic per market_id, in range [0, 6] bps.
        return sum(ord(char) for char in market_id) % 7

    @staticmethod
    def _apply_slippage(limit_price: float, side: OutcomeSide, slippage_bps: int) -> float:
        del side  # Same deterministic slippage model for YES and NO in paper mode.
        slipped = limit_price + (slippage_bps / 10_000.0)
        return round(min(max(slipped, 0.01), 0.99), 4)


class ShadowSignExecutor:
    def __init__(self) -> None:
        self.last_attempt: TransactionAttempt | None = None

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        intent_id = _intent_id(order)
        signature = _shadow_signature(intent_id)
        message = f"shadow_sign_rehearsed signature={signature[:18]}"
        submitted_at = datetime.now(UTC)
        self.last_attempt = TransactionAttempt(
            market_id=order.market_id,
            execution_mode=ExecutionMode.SHADOW_SIGN,
            lane="shadow_sign",
            intent_id=intent_id,
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=ExecutionStatus.SUBMITTED,
            tx_hash=None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=submitted_at,
            confirmation_status=TxConfirmationStatus.PENDING,
            message=message,
            metadata=(f"signature={signature}",),
        )
        return ExecutionResult(
            market_id=order.market_id,
            execution_mode=ExecutionMode.SHADOW_SIGN,
            status=ExecutionStatus.SUBMITTED,
            side=order.side,
            stake_usd=order.stake_usd,
            fill_price=None,
            order_id=f"shadow-{intent_id}",
            intent_id=intent_id,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=submitted_at,
            confirmation_status=TxConfirmationStatus.PENDING,
            message=message,
        )


class LiveDisabledExecutor:
    def __init__(self) -> None:
        self.last_attempt: TransactionAttempt | None = None

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        intent_id = _intent_id(order)
        message = "live_execution_path_disabled"
        submitted_at = datetime.now(UTC)
        self.last_attempt = TransactionAttempt(
            market_id=order.market_id,
            execution_mode=ExecutionMode.LIVE_DISABLED,
            lane="disabled",
            intent_id=intent_id,
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=ExecutionStatus.SKIPPED,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=submitted_at,
            confirmation_status=TxConfirmationStatus.UNKNOWN,
            message=message,
        )
        return ExecutionResult(
            market_id=order.market_id,
            execution_mode=ExecutionMode.LIVE_DISABLED,
            status=ExecutionStatus.SKIPPED,
            side=order.side,
            stake_usd=0.0,
            fill_price=None,
            order_id=f"disabled-{intent_id}",
            intent_id=intent_id,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=submitted_at,
            confirmation_status=TxConfirmationStatus.UNKNOWN,
            message=message,
        )


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

    def run(self, risk: RiskDecision, prediction: PredictionResult) -> ExecutionResult:
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

        order = self.build_order_intent(risk, prediction)
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


def _intent_id(order: OrderIntent) -> str:
    payload = (
        f"{order.run_id}|{order.review_queue_id}|{order.market_id}|{order.venue}|{order.side.value}|"
        f"{order.stake_usd:.6f}|{order.limit_price:.6f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _shadow_signature(intent_id: str) -> str:
    return hashlib.sha256(f"shadow-sign:{intent_id}".encode("utf-8")).hexdigest()


# Backward-compatible name.
DryRunExecutor = PaperExecutor
