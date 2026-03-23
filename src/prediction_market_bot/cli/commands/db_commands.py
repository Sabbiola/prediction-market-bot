from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.secrets import resolve_operational_db_dsn
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure import (
    OperationalBackupError,
    create_sqlite_backup,
    latest_sqlite_backup,
    restore_sqlite_backup,
    verify_operational_sqlite_db,
)
from prediction_market_bot.infrastructure.operational_migrations import (
    MigrationDialect,
    OperationalMigrationError,
    current_operational_schema_version,
    get_operational_schema_status,
    init_operational_schema,
    upgrade_operational_schema,
)
from prediction_market_bot.cli.common import require_cli_role


def _resolved_dialect(driver_value: str) -> MigrationDialect:
    driver = driver_value.strip().lower() or "sqlite"
    if driver == "sqlite":
        return "sqlite"
    if driver == "postgres":
        return "postgres"
    raise OperationalMigrationError(f"Unsupported operational_db driver: {driver}")


def _operational_db_target(*, driver: MigrationDialect, settings: AppSettings) -> str:
    storage = settings.storage
    if driver == "sqlite":
        target = str(storage.operational_db_path).strip()
        if not target:
            raise OperationalMigrationError("storage.operational_db.path is required for sqlite driver.")
        return target
    try:
        dsn = resolve_operational_db_dsn(settings)
    except Exception as exc:
        raise OperationalMigrationError(f"failed to resolve postgres dsn from secrets provider: {exc}") from exc
    if not dsn:
        raise OperationalMigrationError("storage.operational_db.dsn is required for postgres driver.")
    return dsn


def db_init_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    target = _operational_db_target(driver=driver, settings=settings)
    try:
        status = init_operational_schema(target, dialect=driver)
    except OperationalMigrationError as exc:
        print(f"Operational DB init failed: {exc}")
        return 1

    payload = status.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB initialized "
            f"current={status.current_version or 'none'} latest={status.latest_version} "
            f"pending={len(status.pending_versions)}"
        )
    return 0


def db_upgrade_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    target = _operational_db_target(driver=driver, settings=settings)
    try:
        status = upgrade_operational_schema(target, dialect=driver)
    except OperationalMigrationError as exc:
        print(f"Operational DB upgrade failed: {exc}")
        return 1
    payload = status.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB upgraded "
            f"current={status.current_version or 'none'} latest={status.latest_version} "
            f"pending={len(status.pending_versions)}"
        )
    return 0


def db_current_version_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    target = _operational_db_target(driver=driver, settings=settings)
    try:
        status = get_operational_schema_status(target, dialect=driver)
        current = current_operational_schema_version(target, dialect=driver)
    except OperationalMigrationError as exc:
        print(f"Operational DB version check failed: {exc}")
        return 1
    payload = status.to_dict()
    payload["current_version"] = current
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB schema "
            f"current={payload['current_version'] or 'none'} "
            f"latest={payload['latest_version']} "
            f"up_to_date={payload['up_to_date']}"
        )
    return 0 if bool(payload["up_to_date"]) else 2


def db_backup_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    backup_dir: Path | None = None,
    label: str = "",
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    if driver != "sqlite":
        print(f"Operational DB backup is only supported for sqlite driver. Current driver: {driver}")
        return 1
    resolved_backup_dir = str(backup_dir) if backup_dir is not None else settings.storage.operational_db_backup_dir
    try:
        result = create_sqlite_backup(
            db_path=settings.storage.operational_db_path,
            backup_dir=resolved_backup_dir,
            label=label,
        )
    except OperationalBackupError as exc:
        print(f"Operational DB backup failed: {exc}")
        return 1
    payload = result.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB backup created "
            f"path={payload['backup_path']} size_bytes={payload['file_size_bytes']}"
        )
    return 0


def db_restore_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    backup_file: Path | None = None,
    backup_dir: Path | None = None,
    force: bool = False,
    as_json: bool = False,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="db-restore",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"db-restore blocked: {exc}")
        return 2
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    if driver != "sqlite":
        print(f"Operational DB restore is only supported for sqlite driver. Current driver: {driver}")
        return 1

    resolved_backup_dir = str(backup_dir) if backup_dir is not None else settings.storage.operational_db_backup_dir
    selected_backup = backup_file
    if selected_backup is None:
        # --latest and default behavior both resolve the latest immutable snapshot.
        latest = latest_sqlite_backup(resolved_backup_dir)
        if latest is None:
            print(f"Operational DB restore failed: no backup files found in {resolved_backup_dir}")
            return 1
        selected_backup = latest
    try:
        restore_result = restore_sqlite_backup(
            backup_file=selected_backup,
            target_db_path=settings.storage.operational_db_path,
            force_overwrite=force,
            verify_after_restore=True,
        )
    except OperationalBackupError as exc:
        print(f"Operational DB restore failed: {exc}")
        return 1

    verify_result = verify_operational_sqlite_db(settings.storage.operational_db_path)
    payload = {
        "restore": restore_result.to_dict(),
        "verify": verify_result.to_dict(),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB restore completed "
            f"backup={restore_result.backup_path} target={restore_result.target_db_path} verify_ok={verify_result.ok}"
        )
    return 0 if verify_result.ok else 2


def db_verify_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    driver = _resolved_dialect(settings.storage.operational_db_driver)
    if driver == "sqlite":
        result = verify_operational_sqlite_db(settings.storage.operational_db_path)
        payload = result.to_dict()
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                "Operational DB verify "
                f"db={payload['db_path']} exists={payload['exists']} "
                f"integrity_ok={payload['integrity_ok']} up_to_date={payload['schema_up_to_date']}"
            )
        return 0 if result.ok else 2

    target = _operational_db_target(driver=driver, settings=settings)
    try:
        status = get_operational_schema_status(target, dialect=driver)
    except OperationalMigrationError as exc:
        print(f"Operational DB verification failed: {exc}")
        return 1
    payload = status.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Operational DB verify "
            f"dialect={payload['dialect']} "
            f"current={payload['current_version'] or 'none'} "
            f"latest={payload['latest_version']} "
            f"up_to_date={payload['up_to_date']}"
        )
    return 0 if bool(payload["up_to_date"]) else 2
