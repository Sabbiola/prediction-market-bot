from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from prediction_market_bot.domain.models import (
    PendingSettlementRequest,
    TradeReviewCandidate,
    TradeReviewDecision,
    TxAttempt,
    TxIntent,
    TxReceipt,
)


@runtime_checkable
class RunsRepositoryPort(Protocol):
    def upsert_run(self, run_id: str, payload: Mapping[str, Any]) -> None:
        ...


@runtime_checkable
class ReviewQueueRepositoryPort(Protocol):
    def upsert_candidate(self, candidate: TradeReviewCandidate) -> None:
        ...

    def get_candidate(self, queue_id: str) -> TradeReviewCandidate | None:
        ...

    def list_candidates(self, *, run_id: str | None = None, limit: int = 0) -> list[TradeReviewCandidate]:
        ...


@runtime_checkable
class ReviewDecisionRepositoryPort(Protocol):
    def append_decision(self, decision: TradeReviewDecision) -> None:
        ...

    def list_decisions(self, *, queue_id: str | None = None, run_id: str | None = None) -> list[TradeReviewDecision]:
        ...


@runtime_checkable
class OpenPositionsRepositoryPort(Protocol):
    def replace_snapshot(self, payload: Mapping[str, Any]) -> None:
        ...

    def load_snapshot(self) -> Mapping[str, Any] | None:
        ...


@runtime_checkable
class PendingSettlementsRepositoryPort(Protocol):
    def upsert_request(self, request: PendingSettlementRequest) -> None:
        ...

    def get_request(self, request_id: str) -> PendingSettlementRequest | None:
        ...

    def list_requests(
        self,
        *,
        run_id: str | None = None,
        state: str | None = None,
        limit: int = 0,
    ) -> list[PendingSettlementRequest]:
        ...


@runtime_checkable
class TransactionIntentsRepositoryPort(Protocol):
    def upsert_intent(self, run_id: str, intent: TxIntent) -> None:
        ...

    def get_intent(self, intent_id: str) -> TxIntent | None:
        ...

    def list_intents(self, *, run_id: str | None = None, limit: int = 0) -> list[TxIntent]:
        ...


@runtime_checkable
class TransactionAttemptsRepositoryPort(Protocol):
    def append_attempt(self, run_id: str, attempt: TxAttempt) -> None:
        ...

    def list_attempts(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 0,
    ) -> list[TxAttempt]:
        ...


@runtime_checkable
class TransactionReceiptsRepositoryPort(Protocol):
    def append_receipt(self, run_id: str, receipt: TxReceipt) -> None:
        ...

    def list_receipts(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 0,
    ) -> list[TxReceipt]:
        ...


@runtime_checkable
class OperatorControlStateRepositoryPort(Protocol):
    def load_state(self) -> Mapping[str, Any] | None:
        ...

    def save_state(self, payload: Mapping[str, Any]) -> None:
        ...
