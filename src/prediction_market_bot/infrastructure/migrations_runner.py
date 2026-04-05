from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .migrations_registry import (
    MigrationDialect,
    OperationalMigrationError,
    OperationalSchemaStatus,
    _OPERATIONAL_MIGRATIONS,
    latest_operational_schema_version,
)


def init_operational_schema(
    db_path: str | Path,
    *,
    dialect: MigrationDialect = "sqlite",
) -> OperationalSchemaStatus:
    return upgrade_operational_schema(db_path, dialect=dialect)


def upgrade_operational_schema(
    db_path: str | Path,
    *,
    dialect: MigrationDialect = "sqlite",
) -> OperationalSchemaStatus:
    if dialect == "sqlite":
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_schema_migrations_table_sqlite(conn)
            applied = _applied_versions_sqlite(conn)
            for migration in _OPERATIONAL_MIGRATIONS:
                expected_checksum = migration.checksum_for("sqlite")
                existing_checksum = applied.get(migration.version)
                if existing_checksum is not None:
                    if existing_checksum != expected_checksum:
                        raise OperationalMigrationError(
                            f"Migration checksum mismatch for version={migration.version}. "
                            "Database state may be inconsistent."
                        )
                    continue
                conn.executescript(migration.sql_for("sqlite"))
                conn.execute(
                    """
                    INSERT INTO schema_migrations (version, name, checksum, applied_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (migration.version, migration.name, expected_checksum, datetime.now(UTC).isoformat()),
                )
            conn.commit()
        return get_operational_schema_status(path, dialect=dialect)

    if dialect == "postgres":
        dsn = str(db_path).strip()
        if not dsn:
            raise OperationalMigrationError("Operational Postgres DSN is required.")
        psycopg = _load_psycopg()
        try:
            with psycopg.connect(dsn, autocommit=False) as conn:
                with conn.cursor() as cur:
                    _ensure_schema_migrations_table_postgres(cur)
                    applied = _applied_versions_postgres(cur)
                    for migration in _OPERATIONAL_MIGRATIONS:
                        expected_checksum = migration.checksum_for("postgres")
                        existing_checksum = applied.get(migration.version)
                        if existing_checksum is not None:
                            if existing_checksum != expected_checksum:
                                raise OperationalMigrationError(
                                    f"Migration checksum mismatch for version={migration.version}. "
                                    "Database state may be inconsistent."
                                )
                            continue
                        cur.execute(migration.sql_for("postgres"))
                        cur.execute(
                            """
                            INSERT INTO schema_migrations (version, name, checksum, applied_at)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (migration.version, migration.name, expected_checksum, datetime.now(UTC).isoformat()),
                        )
                conn.commit()
        except OperationalMigrationError:
            raise
        except Exception as exc:
            raise OperationalMigrationError(f"Operational postgres migration failed: {exc}") from exc
        return get_operational_schema_status(dsn, dialect=dialect)

    raise OperationalMigrationError(f"Unsupported migration dialect: {dialect}")


def current_operational_schema_version(
    db_path: str | Path,
    *,
    dialect: MigrationDialect = "sqlite",
) -> str | None:
    return get_operational_schema_status(db_path, dialect=dialect).current_version


def get_operational_schema_status(
    db_path: str | Path,
    *,
    dialect: MigrationDialect = "sqlite",
) -> OperationalSchemaStatus:
    latest = latest_operational_schema_version()

    if dialect == "sqlite":
        path = Path(db_path)
        if not path.exists():
            pending = tuple(migration.version for migration in _OPERATIONAL_MIGRATIONS)
            return OperationalSchemaStatus(
                dialect="sqlite",
                db_path=str(path),
                exists=False,
                current_version=None,
                latest_version=latest,
                pending_versions=pending,
                applied_count=0,
            )
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            if not _has_schema_migrations_table_sqlite(conn):
                pending = tuple(migration.version for migration in _OPERATIONAL_MIGRATIONS)
                return OperationalSchemaStatus(
                    dialect="sqlite",
                    db_path=str(path),
                    exists=True,
                    current_version=None,
                    latest_version=latest,
                    pending_versions=pending,
                    applied_count=0,
                )
            applied = _applied_versions_sqlite(conn)
        return _status_from_applied(
            applied=applied,
            latest=latest,
            db_path=str(path),
            dialect="sqlite",
        )

    if dialect == "postgres":
        dsn = str(db_path).strip()
        if not dsn:
            raise OperationalMigrationError("Operational Postgres DSN is required.")
        psycopg = _load_psycopg()
        try:
            with psycopg.connect(dsn, autocommit=False) as conn:
                with conn.cursor() as cur:
                    has_table = _has_schema_migrations_table_postgres(cur)
                    if not has_table:
                        pending = tuple(migration.version for migration in _OPERATIONAL_MIGRATIONS)
                        return OperationalSchemaStatus(
                            dialect="postgres",
                            db_path=_redact_postgres_dsn(dsn),
                            exists=True,
                            current_version=None,
                            latest_version=latest,
                            pending_versions=pending,
                            applied_count=0,
                        )
                    applied = _applied_versions_postgres(cur)
        except Exception as exc:
            raise OperationalMigrationError(f"Operational postgres schema status failed: {exc}") from exc
        return _status_from_applied(
            applied=applied,
            latest=latest,
            db_path=_redact_postgres_dsn(dsn),
            dialect="postgres",
        )

    raise OperationalMigrationError(f"Unsupported migration dialect: {dialect}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _status_from_applied(
    *,
    applied: dict[str, str],
    latest: str,
    db_path: str,
    dialect: MigrationDialect,
) -> OperationalSchemaStatus:
    migration_versions = [migration.version for migration in _OPERATIONAL_MIGRATIONS]
    pending = tuple(version for version in migration_versions if version not in applied)
    current = sorted(applied.keys())[-1] if applied else None
    return OperationalSchemaStatus(
        dialect=dialect,
        db_path=db_path,
        exists=True,
        current_version=current,
        latest_version=latest,
        pending_versions=pending,
        applied_count=len(applied),
    )


def _has_schema_migrations_table_sqlite(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name='schema_migrations'
        """
    ).fetchone()
    return row is not None


def _ensure_schema_migrations_table_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions_sqlite(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version ASC").fetchall()
    result: dict[str, str] = {}
    for row in rows:
        version = str(row["version"]).strip()
        checksum = str(row["checksum"]).strip()
        if version:
            result[version] = checksum
    return result


def _has_schema_migrations_table_postgres(cur: Any) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'schema_migrations'
        )
        """
    )
    row = cur.fetchone()
    return bool(row and row[0])


def _ensure_schema_migrations_table_postgres(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions_postgres(cur: Any) -> dict[str, str]:
    cur.execute("SELECT version, checksum FROM schema_migrations ORDER BY version ASC")
    rows = cur.fetchall() or []
    result: dict[str, str] = {}
    for row in rows:
        version = str(row[0]).strip()
        checksum = str(row[1]).strip()
        if version:
            result[version] = checksum
    return result


def _load_psycopg() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ModuleNotFoundError as exc:
        raise OperationalMigrationError(
            "psycopg is required for postgres operational DB support. "
            "Install optional dependency: pip install 'prediction-market-bot[postgres]'"
        ) from exc


def _redact_postgres_dsn(dsn: str) -> str:
    try:
        parsed = urlsplit(dsn)
    except Exception:
        return "postgres://***"
    username = parsed.username or ""
    password = parsed.password
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if username:
        netloc = username
        if password is not None:
            netloc += ":***"
        netloc += "@"
        netloc += host + port
    else:
        netloc = host + port
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
