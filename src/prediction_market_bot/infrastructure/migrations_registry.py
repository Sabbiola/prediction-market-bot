from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

MigrationDialect = Literal["sqlite", "postgres"]


@dataclass(slots=True, frozen=True)
class OperationalMigration:
    version: str
    name: str
    sqlite_sql: str
    postgres_sql: str | None = None

    def sql_for(self, dialect: MigrationDialect) -> str:
        if dialect == "sqlite":
            return self.sqlite_sql
        if dialect == "postgres":
            if self.postgres_sql:
                return self.postgres_sql
            raise OperationalMigrationError(
                f"Migration {self.version} has no postgres SQL. Dialect support is not ready."
            )
        raise OperationalMigrationError(f"Unsupported migration dialect: {dialect}")

    def checksum_for(self, dialect: MigrationDialect) -> str:
        return sha256(self.sql_for(dialect).encode("utf-8")).hexdigest()

    @property
    def checksum(self) -> str:
        # Backward-compatible alias used by existing sqlite migration state.
        return self.checksum_for("sqlite")


@dataclass(slots=True, frozen=True)
class OperationalSchemaStatus:
    dialect: MigrationDialect
    db_path: str
    exists: bool
    current_version: str | None
    latest_version: str
    pending_versions: tuple[str, ...]
    applied_count: int

    @property
    def up_to_date(self) -> bool:
        return not self.pending_versions and self.current_version == self.latest_version

    def to_dict(self) -> dict[str, object]:
        return {
            "dialect": self.dialect,
            "db_path": self.db_path,
            "exists": self.exists,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "pending_versions": list(self.pending_versions),
            "applied_count": self.applied_count,
            "up_to_date": self.up_to_date,
        }


class OperationalMigrationError(RuntimeError):
    pass


BASELINE_0001 = OperationalMigration(
    version="0001",
    name="baseline_operational_schema",
    sqlite_sql="""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS review_queue (
        queue_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        market_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_review_queue_run_id ON review_queue (run_id);

    CREATE TABLE IF NOT EXISTS review_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_review_decisions_queue_id ON review_decisions (queue_id);
    CREATE INDEX IF NOT EXISTS idx_review_decisions_run_id ON review_decisions (run_id);

    CREATE TABLE IF NOT EXISTS open_positions_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pending_settlements (
        request_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pending_settlements_run_id ON pending_settlements (run_id);
    CREATE INDEX IF NOT EXISTS idx_pending_settlements_state ON pending_settlements (state);

    CREATE TABLE IF NOT EXISTS transaction_intents (
        intent_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_intents_run_id ON transaction_intents (run_id);

    CREATE TABLE IF NOT EXISTS transaction_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_attempts_run_id ON transaction_attempts (run_id);
    CREATE INDEX IF NOT EXISTS idx_transaction_attempts_intent_id ON transaction_attempts (intent_id);

    CREATE TABLE IF NOT EXISTS transaction_receipts (
        receipt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_receipts_run_id ON transaction_receipts (run_id);

    CREATE TABLE IF NOT EXISTS operator_control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    """,
    postgres_sql="""
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS review_queue (
        queue_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        market_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_review_queue_run_id ON review_queue (run_id);

    CREATE TABLE IF NOT EXISTS review_decisions (
        id BIGSERIAL PRIMARY KEY,
        queue_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_review_decisions_queue_id ON review_decisions (queue_id);
    CREATE INDEX IF NOT EXISTS idx_review_decisions_run_id ON review_decisions (run_id);

    CREATE TABLE IF NOT EXISTS open_positions_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pending_settlements (
        request_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pending_settlements_run_id ON pending_settlements (run_id);
    CREATE INDEX IF NOT EXISTS idx_pending_settlements_state ON pending_settlements (state);

    CREATE TABLE IF NOT EXISTS transaction_intents (
        intent_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_intents_run_id ON transaction_intents (run_id);

    CREATE TABLE IF NOT EXISTS transaction_attempts (
        id BIGSERIAL PRIMARY KEY,
        run_id TEXT NOT NULL,
        intent_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_attempts_run_id ON transaction_attempts (run_id);
    CREATE INDEX IF NOT EXISTS idx_transaction_attempts_intent_id ON transaction_attempts (intent_id);

    CREATE TABLE IF NOT EXISTS transaction_receipts (
        receipt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_transaction_receipts_run_id ON transaction_receipts (run_id);

    CREATE TABLE IF NOT EXISTS operator_control_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    """,
)

_OPERATIONAL_MIGRATIONS: tuple[OperationalMigration, ...] = (BASELINE_0001,)

# Public alias used by callers
MIGRATION_REGISTRY: tuple[OperationalMigration, ...] = _OPERATIONAL_MIGRATIONS


def operational_migrations() -> tuple[OperationalMigration, ...]:
    return _OPERATIONAL_MIGRATIONS


def latest_operational_schema_version() -> str:
    return _OPERATIONAL_MIGRATIONS[-1].version
