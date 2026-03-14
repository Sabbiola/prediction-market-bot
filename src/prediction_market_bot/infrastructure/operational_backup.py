from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from prediction_market_bot.infrastructure.operational_migrations import (
    OperationalMigrationError,
    get_operational_schema_status,
)


@dataclass(slots=True, frozen=True)
class OperationalBackupResult:
    backup_path: str
    source_db_path: str
    created_at: str
    file_size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_path": self.backup_path,
            "source_db_path": self.source_db_path,
            "created_at": self.created_at,
            "file_size_bytes": self.file_size_bytes,
        }


@dataclass(slots=True, frozen=True)
class OperationalRestoreResult:
    backup_path: str
    target_db_path: str
    restored_at: str
    file_size_bytes: int
    integrity_ok: bool
    integrity_detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "backup_path": self.backup_path,
            "target_db_path": self.target_db_path,
            "restored_at": self.restored_at,
            "file_size_bytes": self.file_size_bytes,
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
        }


@dataclass(slots=True, frozen=True)
class OperationalDbVerifyResult:
    db_path: str
    exists: bool
    integrity_ok: bool
    integrity_detail: str
    schema_up_to_date: bool
    schema_current_version: str | None
    schema_latest_version: str | None
    schema_pending_versions: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.exists and self.integrity_ok and self.schema_up_to_date

    def to_dict(self) -> dict[str, object]:
        return {
            "db_path": self.db_path,
            "exists": self.exists,
            "integrity_ok": self.integrity_ok,
            "integrity_detail": self.integrity_detail,
            "schema_up_to_date": self.schema_up_to_date,
            "schema_current_version": self.schema_current_version,
            "schema_latest_version": self.schema_latest_version,
            "schema_pending_versions": list(self.schema_pending_versions),
            "ok": self.ok,
        }


class OperationalBackupError(RuntimeError):
    pass


def create_sqlite_backup(
    *,
    db_path: str | Path,
    backup_dir: str | Path,
    label: str = "",
    now_fn: Callable[[], datetime] | None = None,
) -> OperationalBackupResult:
    source = Path(db_path)
    if not source.exists():
        raise OperationalBackupError(f"Operational DB not found: {source}")
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    now = (now_fn or (lambda: datetime.now(UTC)))()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    suffix = _safe_suffix(label)
    filename = f"operational-{stamp}{('-' + suffix) if suffix else ''}.sqlite3"
    target = target_dir / filename
    if target.exists():
        raise OperationalBackupError(f"Backup target already exists: {target}")

    with sqlite3.connect(source) as src_conn:
        with sqlite3.connect(target) as dst_conn:
            src_conn.backup(dst_conn)
            dst_conn.commit()

    return OperationalBackupResult(
        backup_path=str(target),
        source_db_path=str(source),
        created_at=now.isoformat(),
        file_size_bytes=target.stat().st_size,
    )


def list_sqlite_backups(backup_dir: str | Path) -> list[Path]:
    root = Path(backup_dir)
    if not root.exists():
        return []
    rows = sorted((path for path in root.glob("*.sqlite3") if path.is_file()), key=lambda p: p.name)
    return rows


def latest_sqlite_backup(backup_dir: str | Path) -> Path | None:
    rows = list_sqlite_backups(backup_dir)
    if not rows:
        return None
    return rows[-1]


def restore_sqlite_backup(
    *,
    backup_file: str | Path,
    target_db_path: str | Path,
    force_overwrite: bool = False,
    verify_after_restore: bool = True,
) -> OperationalRestoreResult:
    source_backup = Path(backup_file)
    if not source_backup.exists():
        raise OperationalBackupError(f"Backup file not found: {source_backup}")
    if source_backup.suffix.lower() != ".sqlite3":
        raise OperationalBackupError(f"Backup file must use .sqlite3 extension: {source_backup}")

    target = Path(target_db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force_overwrite:
        raise OperationalBackupError(
            "Restore blocked: target DB already exists. Re-run with --force to overwrite."
        )

    tmp_target = target.parent / f"{target.name}.restore_tmp"
    if tmp_target.exists():
        tmp_target.unlink()
    shutil.copy2(source_backup, tmp_target)
    if target.exists():
        target.unlink()
    tmp_target.replace(target)

    integrity_ok = True
    integrity_detail = "integrity_check_ok"
    if verify_after_restore:
        integrity_ok, integrity_detail = _sqlite_integrity_check(target)

    restored_at = datetime.now(UTC).isoformat()
    return OperationalRestoreResult(
        backup_path=str(source_backup),
        target_db_path=str(target),
        restored_at=restored_at,
        file_size_bytes=target.stat().st_size,
        integrity_ok=integrity_ok,
        integrity_detail=integrity_detail,
    )


def verify_operational_sqlite_db(db_path: str | Path) -> OperationalDbVerifyResult:
    path = Path(db_path)
    exists = path.exists()
    if not exists:
        return OperationalDbVerifyResult(
            db_path=str(path),
            exists=False,
            integrity_ok=False,
            integrity_detail="db_not_found",
            schema_up_to_date=False,
            schema_current_version=None,
            schema_latest_version=None,
            schema_pending_versions=(),
        )

    integrity_ok, integrity_detail = _sqlite_integrity_check(path)
    try:
        schema_status = get_operational_schema_status(path, dialect="sqlite")
        schema_up_to_date = schema_status.up_to_date
        schema_current_version = schema_status.current_version
        schema_latest_version = schema_status.latest_version
        schema_pending_versions = schema_status.pending_versions
    except OperationalMigrationError as exc:
        schema_up_to_date = False
        schema_current_version = None
        schema_latest_version = None
        schema_pending_versions = ()
        if integrity_ok:
            integrity_ok = False
            integrity_detail = f"schema_status_failed: {exc}"

    return OperationalDbVerifyResult(
        db_path=str(path),
        exists=True,
        integrity_ok=integrity_ok,
        integrity_detail=integrity_detail,
        schema_up_to_date=schema_up_to_date,
        schema_current_version=schema_current_version,
        schema_latest_version=schema_latest_version,
        schema_pending_versions=schema_pending_versions,
    )


def _sqlite_integrity_check(db_path: Path) -> tuple[bool, str]:
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
    except Exception as exc:
        return False, f"integrity_check_failed: {exc}"
    if row is None:
        return False, "integrity_check_no_result"
    value = str(row[0]).strip().lower()
    if value == "ok":
        return True, "integrity_check_ok"
    return False, f"integrity_check_failed: {value}"


def _safe_suffix(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    cleaned = cleaned.strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:64]
