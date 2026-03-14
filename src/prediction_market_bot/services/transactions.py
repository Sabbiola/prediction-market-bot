from __future__ import annotations

from dataclasses import dataclass

from prediction_market_bot.domain.enums import ExecutionMode, TxConfirmationStatus
from prediction_market_bot.domain.models import TxAttempt, TxIntent
from prediction_market_bot.infrastructure.sandbox_chain import SandboxChainExecutor
from prediction_market_bot.interfaces import (
    TransactionAttemptsRepositoryPort,
    TransactionIntentsRepositoryPort,
    TransactionReceiptsRepositoryPort,
)


@dataclass(slots=True, frozen=True)
class TxStatusSnapshot:
    intent_id: str
    run_id: str
    market_id: str
    execution_mode: ExecutionMode
    review_queue_id: str
    side: str
    stake_usd: float
    latest_tx_hash: str
    nonce: int | None
    retry_count: int
    confirmation_status: TxConfirmationStatus
    submitted_at: str
    confirmed_at: str
    replacement_for_tx_hash: str
    replaced_by_tx_hash: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "run_id": self.run_id,
            "market_id": self.market_id,
            "execution_mode": self.execution_mode.value,
            "review_queue_id": self.review_queue_id,
            "side": self.side,
            "stake_usd": self.stake_usd,
            "latest_tx_hash": self.latest_tx_hash,
            "nonce": self.nonce,
            "retry_count": self.retry_count,
            "confirmation_status": self.confirmation_status.value,
            "submitted_at": self.submitted_at,
            "confirmed_at": self.confirmed_at,
            "replacement_for_tx_hash": self.replacement_for_tx_hash,
            "replaced_by_tx_hash": self.replaced_by_tx_hash,
            "message": self.message,
        }


class SandboxTransactionService:
    def __init__(
        self,
        *,
        executor: SandboxChainExecutor,
        intent_repo: TransactionIntentsRepositoryPort,
        attempt_repo: TransactionAttemptsRepositoryPort,
        receipt_repo: TransactionReceiptsRepositoryPort,
    ) -> None:
        self.executor = executor
        self.intent_repo = intent_repo
        self.attempt_repo = attempt_repo
        self.receipt_repo = receipt_repo

    def list_status(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 50,
    ) -> list[TxStatusSnapshot]:
        if intent_id is not None and intent_id.strip():
            intents: list[TxIntent] = []
            found = self.intent_repo.get_intent(intent_id.strip())
            if found is not None:
                intents.append(found)
        else:
            intents = self.intent_repo.list_intents(run_id=run_id, limit=limit)

        snapshots: list[TxStatusSnapshot] = []
        for intent in intents:
            tx_intent_id = self._intent_id(intent)
            attempts = self.attempt_repo.list_attempts(intent_id=tx_intent_id, limit=1)
            latest = attempts[0] if attempts else None
            if latest is None:
                snapshots.append(
                    TxStatusSnapshot(
                        intent_id=tx_intent_id,
                        run_id=intent.run_id,
                        market_id=intent.market_id,
                        execution_mode=ExecutionMode.SANDBOX_CHAIN,
                        review_queue_id=intent.review_queue_id,
                        side=intent.side.value,
                        stake_usd=intent.stake_usd,
                        latest_tx_hash="",
                        nonce=None,
                        retry_count=0,
                        confirmation_status=TxConfirmationStatus.UNKNOWN,
                        submitted_at="",
                        confirmed_at="",
                        replacement_for_tx_hash="",
                        replaced_by_tx_hash="",
                        message="tx_attempt_missing",
                    )
                )
                continue
            snapshots.append(self._snapshot_from_attempt(latest))
        return snapshots[:limit] if limit > 0 else snapshots

    def reconcile(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 100,
    ) -> list[TxStatusSnapshot]:
        snapshots: list[TxStatusSnapshot] = []
        source = self.list_status(run_id=run_id, intent_id=intent_id, limit=limit)
        for item in source:
            if item.confirmation_status in {TxConfirmationStatus.MINED, TxConfirmationStatus.FAILED}:
                snapshots.append(item)
                continue
            attempts = self.attempt_repo.list_attempts(intent_id=item.intent_id, limit=1)
            if not attempts:
                snapshots.append(item)
                continue
            updated_attempt, receipt = self.executor.reconcile_attempt(attempts[0])
            self.attempt_repo.append_attempt(updated_attempt.run_id, updated_attempt)
            self.receipt_repo.append_receipt(updated_attempt.run_id, receipt)
            snapshots.append(self._snapshot_from_attempt(updated_attempt))
        return snapshots

    def resubmit_safe(self, *, intent_id: str) -> TxStatusSnapshot:
        tx_intent = self.intent_repo.get_intent(intent_id.strip())
        if tx_intent is None:
            raise KeyError(f"tx intent not found: {intent_id}")
        attempts = self.attempt_repo.list_attempts(intent_id=intent_id.strip(), limit=1)
        previous = attempts[0] if attempts else None
        receipt, attempt = self.executor.resubmit_safe(tx_intent, previous)
        self.attempt_repo.append_attempt(tx_intent.run_id, attempt)
        self.receipt_repo.append_receipt(tx_intent.run_id, receipt)
        if previous is not None and previous.tx_hash:
            replaced = previous.model_copy(
                update={
                    "confirmation_status": TxConfirmationStatus.REPLACED,
                    "replaced_by_tx_hash": attempt.tx_hash or "",
                    "message": "replaced_via_tx_resubmit_safe",
                }
            )
            self.attempt_repo.append_attempt(previous.run_id, replaced)
        return self._snapshot_from_attempt(attempt)

    @staticmethod
    def _snapshot_from_attempt(attempt: TxAttempt) -> TxStatusSnapshot:
        return TxStatusSnapshot(
            intent_id=attempt.intent_id,
            run_id=attempt.run_id,
            market_id=attempt.market_id,
            execution_mode=attempt.execution_mode,
            review_queue_id=attempt.review_queue_id,
            side=attempt.side.value,
            stake_usd=attempt.stake_usd,
            latest_tx_hash=attempt.tx_hash or "",
            nonce=attempt.nonce,
            retry_count=attempt.retry_count,
            confirmation_status=attempt.confirmation_status,
            submitted_at=attempt.submitted_at.isoformat() if attempt.submitted_at else "",
            confirmed_at=attempt.confirmed_at.isoformat() if attempt.confirmed_at else "",
            replacement_for_tx_hash=attempt.replacement_for_tx_hash,
            replaced_by_tx_hash=attempt.replaced_by_tx_hash,
            message=attempt.message,
        )

    @staticmethod
    def _intent_id(intent: TxIntent) -> str:
        payload = (
            f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
            f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
        )
        import hashlib

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
