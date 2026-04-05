from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


# ---------------------------------------------------------------------------
# Lightweight thread-safe SQLite connection pool with WAL mode
# ---------------------------------------------------------------------------


class _SqliteConnectionPool:
    """Reuse a small number of SQLite connections instead of opening one per call."""

    def __init__(self, db_path: Path, *, pool_size: int = 4) -> None:
        self._db_path = db_path
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []

    def acquire(self) -> sqlite3.Connection:
        with self._lock:
            if self._connections:
                return self._connections.pop()
        return self._create_connection()

    def release(self, conn: sqlite3.Connection) -> None:
        with self._lock:
            if len(self._connections) < self._pool_size:
                self._connections.append(conn)
                return
        conn.close()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def close_all(self) -> None:
        with self._lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()


_pool_registry: dict[str, _SqliteConnectionPool] = {}
_pool_registry_lock = threading.Lock()


def _get_pool(db_path: Path) -> _SqliteConnectionPool:
    key = str(db_path.resolve())
    with _pool_registry_lock:
        if key not in _pool_registry:
            _pool_registry[key] = _SqliteConnectionPool(db_path)
        return _pool_registry[key]


def close_pool_for_path(db_path: str | Path) -> None:
    """Close and remove the connection pool for a given database path.

    Useful for test teardown and backup/restore operations on Windows where
    open connections prevent file deletion.
    """
    key = str(Path(db_path).resolve())
    with _pool_registry_lock:
        pool = _pool_registry.pop(key, None)
    if pool is not None:
        pool.close_all()
