from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

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


def operational_migrations() -> tuple[OperationalMigration, ...]:
    return _OPERATIONAL_MIGRATIONS


def latest_operational_schema_version() -> str:
    return _OPERATIONAL_MIGRATIONS[-1].version


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

