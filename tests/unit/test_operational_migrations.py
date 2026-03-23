from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prediction_market_bot.infrastructure.operational_migrations import (
    OperationalMigrationError,
    get_operational_schema_status,
    init_operational_schema,
    latest_operational_schema_version,
    operational_migrations,
    upgrade_operational_schema,
)


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def test_operational_schema_status_reports_pending_on_fresh_db(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    status = get_operational_schema_status(db_path, dialect="sqlite")
    assert status.exists is False
    assert status.current_version is None
    assert status.latest_version == latest_operational_schema_version()
    assert status.pending_versions == tuple(m.version for m in operational_migrations())
    assert status.up_to_date is False


def test_init_operational_schema_applies_baseline_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    status = init_operational_schema(db_path, dialect="sqlite")
    assert status.exists is True
    assert status.current_version == latest_operational_schema_version()
    assert status.pending_versions == ()
    assert status.up_to_date is True

    tables = _table_names(db_path)
    assert "schema_migrations" in tables
    assert "runs" in tables
    assert "review_queue" in tables
    assert "transaction_receipts" in tables


def test_upgrade_operational_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    first = upgrade_operational_schema(db_path, dialect="sqlite")
    second = upgrade_operational_schema(db_path, dialect="sqlite")
    assert first.current_version == second.current_version == latest_operational_schema_version()
    assert first.applied_count == second.applied_count == len(operational_migrations())


def test_operational_migrations_define_postgres_sql() -> None:
    for migration in operational_migrations():
        assert migration.postgres_sql is not None
        assert migration.sql_for("postgres").strip()


def test_postgres_status_requires_dsn() -> None:
    with pytest.raises(OperationalMigrationError, match="DSN"):
        get_operational_schema_status("", dialect="postgres")
