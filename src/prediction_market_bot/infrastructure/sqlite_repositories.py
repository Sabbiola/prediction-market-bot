from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Generator, Mapping

from prediction_market_bot.domain.models import (
    PendingSettlementRequest,
    TradeReviewCandidate,
    TradeReviewDecision,
    TxAttempt,
    TxIntent,
    TxReceipt,
)
from prediction_market_bot.interfaces import (
    OperatorControlStateRepositoryPort,
    OpenPositionsRepositoryPort,
    PendingSettlementsRepositoryPort,
    ReviewDecisionRepositoryPort,
    ReviewQueueRepositoryPort,
    RunsRepositoryPort,
    TransactionAttemptsRepositoryPort,
    TransactionIntentsRepositoryPort,
    TransactionReceiptsRepositoryPort,
)
from prediction_market_bot.infrastructure.sqlite_pool import _get_pool


# ---------------------------------------------------------------------------
# Base repository
# ---------------------------------------------------------------------------


class _SqliteBaseRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._pool = _get_pool(self.db_path)

    # Keep for backward compatibility; internal code should use _connection().
    def _connect(self) -> sqlite3.Connection:
        return self._pool.acquire()

    def _release(self, conn: sqlite3.Connection) -> None:
        self._pool.release(conn)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._pool.acquire()
        try:
            yield conn
        finally:
            self._pool.release(conn)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _dumps(payload: Mapping[str, Any]) -> str:
        return json.dumps(dict(payload), separators=(",", ":"), default=str)

    @staticmethod
    def _loads(payload_json: str) -> dict[str, Any]:
        raw = json.loads(payload_json)
        return dict(raw) if isinstance(raw, dict) else {}


class SqliteRunsRepository(_SqliteBaseRepository, RunsRepositoryPort):
    def upsert_run(self, run_id: str, payload: Mapping[str, Any]) -> None:
        now = self._utc_now()
        status = str(payload.get("status", "")).strip()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, status, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (run_id, status, now, self._dumps(payload)),
            )
            conn.commit()


class SqliteReviewQueueRepository(_SqliteBaseRepository, ReviewQueueRepositoryPort):
    def upsert_candidate(self, candidate: TradeReviewCandidate) -> None:
        payload = candidate.model_dump(mode="json")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO review_queue (queue_id, run_id, market_id, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    market_id=excluded.market_id,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    candidate.queue_id,
                    candidate.run_id,
                    candidate.market_id,
                    self._utc_now(),
                    self._dumps(payload),
                ),
            )
            conn.commit()

    def get_candidate(self, queue_id: str) -> TradeReviewCandidate | None:
        target = queue_id.strip()
        if not target:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM review_queue WHERE queue_id = ?",
                (target,),
            ).fetchone()
        if row is None:
            return None
        try:
            return TradeReviewCandidate.model_validate(self._loads(str(row["payload_json"])), strict=False)
        except Exception:
            return None

    def list_candidates(self, *, run_id: str | None = None, limit: int = 0) -> list[TradeReviewCandidate]:
        query = "SELECT payload_json FROM review_queue"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY updated_at DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        records: list[TradeReviewCandidate] = []
        for row in rows:
            try:
                parsed = TradeReviewCandidate.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            records.append(parsed)
        return records


class SqliteReviewDecisionRepository(_SqliteBaseRepository, ReviewDecisionRepositoryPort):
    def append_decision(self, decision: TradeReviewDecision) -> None:
        payload = decision.model_dump(mode="json")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO review_decisions (queue_id, run_id, decided_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    decision.queue_id,
                    decision.run_id,
                    decision.decided_at.isoformat(),
                    self._dumps(payload),
                ),
            )
            conn.commit()

    def list_decisions(self, *, queue_id: str | None = None, run_id: str | None = None) -> list[TradeReviewDecision]:
        query = "SELECT payload_json FROM review_decisions"
        where: list[str] = []
        params: list[Any] = []
        if queue_id is not None:
            where.append("queue_id = ?")
            params.append(queue_id)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY decided_at ASC, id ASC"
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        records: list[TradeReviewDecision] = []
        for row in rows:
            try:
                parsed = TradeReviewDecision.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            records.append(parsed)
        return records


class SqliteOpenPositionsRepository(_SqliteBaseRepository, OpenPositionsRepositoryPort):
    def replace_snapshot(self, payload: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO open_positions_state (id, updated_at, payload_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (self._utc_now(), self._dumps(payload)),
            )
            conn.commit()

    def load_snapshot(self) -> Mapping[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json FROM open_positions_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return self._loads(str(row["payload_json"]))


class SqlitePendingSettlementsRepository(_SqliteBaseRepository, PendingSettlementsRepositoryPort):
    def upsert_request(self, request: PendingSettlementRequest) -> None:
        payload = request.model_dump(mode="json")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO pending_settlements (request_id, run_id, state, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    state=excluded.state,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    request.request_id,
                    request.run_id,
                    request.state.value,
                    self._utc_now(),
                    self._dumps(payload),
                ),
            )
            conn.commit()

    def get_request(self, request_id: str) -> PendingSettlementRequest | None:
        target = request_id.strip()
        if not target:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM pending_settlements WHERE request_id = ?",
                (target,),
            ).fetchone()
        if row is None:
            return None
        try:
            return PendingSettlementRequest.model_validate(self._loads(str(row["payload_json"])), strict=False)
        except Exception:
            return None

    def list_requests(
        self,
        *,
        run_id: str | None = None,
        state: str | None = None,
        limit: int = 0,
    ) -> list[PendingSettlementRequest]:
        query = "SELECT payload_json FROM pending_settlements"
        where: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if state is not None:
            where.append("state = ?")
            params.append(state)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY updated_at DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        records: list[PendingSettlementRequest] = []
        for row in rows:
            try:
                parsed = PendingSettlementRequest.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            records.append(parsed)
        return records


class SqliteTransactionIntentsRepository(_SqliteBaseRepository, TransactionIntentsRepositoryPort):
    def upsert_intent(self, run_id: str, intent: TxIntent) -> None:
        payload = intent.model_dump(mode="json")
        intent_id = _intent_id(intent)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO transaction_intents (intent_id, run_id, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    created_at=excluded.created_at,
                    payload_json=excluded.payload_json
                """,
                (intent_id, run_id, self._utc_now(), self._dumps(payload)),
            )
            conn.commit()

    def get_intent(self, intent_id: str) -> TxIntent | None:
        target = intent_id.strip()
        if not target:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM transaction_intents WHERE intent_id = ?",
                (target,),
            ).fetchone()
        if row is None:
            return None
        try:
            return TxIntent.model_validate(self._loads(str(row["payload_json"])), strict=False)
        except Exception:
            return None

    def list_intents(self, *, run_id: str | None = None, limit: int = 0) -> list[TxIntent]:
        query = "SELECT payload_json FROM transaction_intents"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        intents: list[TxIntent] = []
        for row in rows:
            try:
                parsed = TxIntent.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            intents.append(parsed)
        return intents


class SqliteTransactionAttemptsRepository(_SqliteBaseRepository, TransactionAttemptsRepositoryPort):
    def append_attempt(self, run_id: str, attempt: TxAttempt) -> None:
        payload = attempt.model_dump(mode="json")
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO transaction_attempts (run_id, intent_id, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, attempt.intent_id, self._utc_now(), self._dumps(payload)),
            )
            conn.commit()

    def list_attempts(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 0,
    ) -> list[TxAttempt]:
        query = "SELECT payload_json FROM transaction_attempts"
        params: list[Any] = []
        where: list[str] = []
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        if intent_id is not None:
            where.append("intent_id = ?")
            params.append(intent_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC, id DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        attempts: list[TxAttempt] = []
        for row in rows:
            try:
                parsed = TxAttempt.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            attempts.append(parsed)
        return attempts


class SqliteTransactionReceiptsRepository(_SqliteBaseRepository, TransactionReceiptsRepositoryPort):
    def append_receipt(self, run_id: str, receipt: TxReceipt) -> None:
        payload = receipt.model_dump(mode="json")
        base = (
            f"{run_id}|{receipt.market_id}|{receipt.intent_id}|{receipt.side.value}|{receipt.status.value}|"
            f"{receipt.order_id or ''}|{receipt.tx_hash or ''}|{receipt.confirmation_status.value}|"
            f"{receipt.confirmed_at.isoformat() if receipt.confirmed_at else ''}"
        )
        receipt_id = sha256(base.encode("utf-8")).hexdigest()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO transaction_receipts (receipt_id, run_id, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(receipt_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    created_at=excluded.created_at,
                    payload_json=excluded.payload_json
                """,
                (receipt_id, run_id, self._utc_now(), self._dumps(payload)),
            )
            conn.commit()

    def list_receipts(
        self,
        *,
        run_id: str | None = None,
        intent_id: str | None = None,
        limit: int = 0,
    ) -> list[TxReceipt]:
        query = "SELECT payload_json FROM transaction_receipts"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        receipts: list[TxReceipt] = []
        for row in rows:
            try:
                parsed = TxReceipt.model_validate(self._loads(str(row["payload_json"])), strict=False)
            except Exception:
                continue
            if intent_id is not None and parsed.intent_id != intent_id:
                continue
            receipts.append(parsed)
        return receipts


class SqliteOperatorControlStateRepository(_SqliteBaseRepository, OperatorControlStateRepositoryPort):
    def load_state(self) -> Mapping[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json FROM operator_control_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return self._loads(str(row["payload_json"]))

    def save_state(self, payload: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO operator_control_state (id, updated_at, payload_json)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (self._utc_now(), self._dumps(payload)),
            )
            conn.commit()


@dataclass(slots=True, frozen=True)
class OperationalRepositories:
    runs: RunsRepositoryPort
    review_queue: ReviewQueueRepositoryPort
    review_decisions: ReviewDecisionRepositoryPort
    open_positions: OpenPositionsRepositoryPort
    pending_settlements: PendingSettlementsRepositoryPort
    transaction_intents: TransactionIntentsRepositoryPort
    transaction_attempts: TransactionAttemptsRepositoryPort
    transaction_receipts: TransactionReceiptsRepositoryPort
    operator_control_state: OperatorControlStateRepositoryPort


def _intent_id(intent: TxIntent) -> str:
    payload = (
        f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
        f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()
