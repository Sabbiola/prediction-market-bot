"""operational_sqlite.py — backward-compat re-export wrapper.

The implementation has been split into focused submodules:
  - sqlite_pool.py          connection pool and pool registry
  - sqlite_repositories.py  all repository classes and OperationalRepositories

Everything that was previously importable from this module remains importable
from this module unchanged.
"""
from __future__ import annotations

from pathlib import Path

from prediction_market_bot.infrastructure.operational_migrations import (
    MigrationDialect,
    upgrade_operational_schema,
)
from prediction_market_bot.infrastructure.sqlite_pool import (
    _SqliteConnectionPool,
    _get_pool,
    _pool_registry,
    _pool_registry_lock,
    close_pool_for_path,
)
from prediction_market_bot.infrastructure.sqlite_repositories import (
    OperationalRepositories,
    SqliteOpenPositionsRepository,
    SqliteOperatorControlStateRepository,
    SqlitePendingSettlementsRepository,
    SqliteReviewDecisionRepository,
    SqliteReviewQueueRepository,
    SqliteRunsRepository,
    SqliteTransactionAttemptsRepository,
    SqliteTransactionIntentsRepository,
    SqliteTransactionReceiptsRepository,
    _SqliteBaseRepository,
    _intent_id,
)


def bootstrap_operational_schema(db_path: str | Path, *, dialect: MigrationDialect = "sqlite") -> None:
    upgrade_operational_schema(db_path, dialect=dialect)


class SqliteOperationalRepositories(OperationalRepositories):
    @classmethod
    def bootstrap(
        cls,
        db_path: str | Path,
        *,
        apply_migrations: bool = True,
        dialect: MigrationDialect = "sqlite",
    ) -> "SqliteOperationalRepositories":
        if apply_migrations:
            bootstrap_operational_schema(db_path, dialect=dialect)
        return cls(
            runs=SqliteRunsRepository(db_path),
            review_queue=SqliteReviewQueueRepository(db_path),
            review_decisions=SqliteReviewDecisionRepository(db_path),
            open_positions=SqliteOpenPositionsRepository(db_path),
            pending_settlements=SqlitePendingSettlementsRepository(db_path),
            transaction_intents=SqliteTransactionIntentsRepository(db_path),
            transaction_attempts=SqliteTransactionAttemptsRepository(db_path),
            transaction_receipts=SqliteTransactionReceiptsRepository(db_path),
            operator_control_state=SqliteOperatorControlStateRepository(db_path),
        )


__all__ = [
    # pool
    "_SqliteConnectionPool",
    "_get_pool",
    "_pool_registry",
    "_pool_registry_lock",
    "close_pool_for_path",
    # base
    "_SqliteBaseRepository",
    # repositories
    "SqliteRunsRepository",
    "SqliteReviewQueueRepository",
    "SqliteReviewDecisionRepository",
    "SqliteOpenPositionsRepository",
    "SqlitePendingSettlementsRepository",
    "SqliteTransactionIntentsRepository",
    "SqliteTransactionAttemptsRepository",
    "SqliteTransactionReceiptsRepository",
    "SqliteOperatorControlStateRepository",
    # aggregates
    "OperationalRepositories",
    "SqliteOperationalRepositories",
    # bootstrap
    "bootstrap_operational_schema",
    # internal helpers (re-exported for completeness)
    "_intent_id",
    "MigrationDialect",
]
