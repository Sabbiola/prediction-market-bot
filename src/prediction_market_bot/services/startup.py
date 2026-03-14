from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prediction_market_bot.app.secrets import build_secret_provider, resolve_operational_db_dsn
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure.operational_migrations import (
    MigrationDialect,
    OperationalMigrationError,
    get_operational_schema_status,
)
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.infrastructure.operational_sqlite import OperationalRepositories

StartupSeverity = Literal["error", "warning", "info"]


@dataclass(slots=True, frozen=True)
class StartupCheck:
    name: str
    ok: bool
    severity: StartupSeverity
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(slots=True, frozen=True)
class StartupValidationReport:
    checks: tuple[StartupCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok or check.severity != "error" for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def validate_startup(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: OperationalRepositories,
) -> StartupValidationReport:
    checks: list[StartupCheck] = []

    try:
        settings.validate_dry_run_only()
        checks.append(StartupCheck("dry_run_guardrail", True, "info", "dry_run_guardrail_ok"))
    except Exception as exc:
        checks.append(StartupCheck("dry_run_guardrail", False, "error", f"dry_run_guardrail_failed: {exc}"))

    checks.extend(
        (
            _check_writable_dir(Path(settings.storage.artifacts_dir), name="artifacts_dir"),
            _check_writable_dir(Path(settings.storage.audit_log_path).parent, name="audit_log_dir"),
        )
    )
    if settings.storage.operational_db_driver.strip().lower() == "sqlite":
        checks.append(_check_writable_dir(Path(settings.storage.operational_db_path).parent, name="operational_db_dir"))
    if settings.metrics.enabled:
        checks.append(_check_writable_dir(Path(settings.metrics.path).parent, name="metrics_dir"))

    db_config_check = _check_operational_db_config(settings)
    checks.append(db_config_check)
    if db_config_check.ok:
        schema_check = _check_operational_schema(settings)
        checks.append(schema_check)
    else:
        schema_check = StartupCheck(
            "operational_schema_version",
            False,
            "warning",
            "operational_schema_status_skipped_due_to_db_config_error",
        )
        checks.append(schema_check)

    if db_config_check.ok and schema_check.ok:
        checks.append(_check_operational_repositories(operational))
    else:
        checks.append(
            StartupCheck(
                "operational_repository_connectivity",
                False,
                "warning",
                "operational_repository_connectivity_skipped_due_to_db_or_schema_mismatch",
            )
        )
    checks.append(_check_persistence_read_write(persistence))
    checks.extend(_check_sandbox_chain_config(settings))
    return StartupValidationReport(checks=tuple(checks))


def _check_writable_dir(path: Path, *, name: str) -> StartupCheck:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".startup_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return StartupCheck(name, True, "info", f"{name}_writable")
    except Exception as exc:
        return StartupCheck(name, False, "error", f"{name}_not_writable: {exc}")


def _check_operational_repositories(operational: OperationalRepositories) -> StartupCheck:
    try:
        operational.review_queue.list_candidates(limit=1)
        operational.review_decisions.list_decisions()
        operational.pending_settlements.list_requests(limit=1)
        operational.transaction_intents.list_intents(limit=1)
        operational.transaction_attempts.list_attempts(limit=1)
        operational.transaction_receipts.list_receipts(limit=1)
        operational.open_positions.load_snapshot()
        operational.operator_control_state.load_state()
        return StartupCheck("operational_repository_connectivity", True, "info", "operational_repository_connectivity_ok")
    except Exception as exc:
        return StartupCheck(
            "operational_repository_connectivity",
            False,
            "error",
            f"operational_repository_connectivity_failed: {exc}",
        )


def _check_operational_schema(settings: AppSettings) -> StartupCheck:
    driver = settings.storage.operational_db_driver.strip().lower() or "sqlite"
    if driver not in {"sqlite", "postgres"}:
        return StartupCheck("operational_schema_version", False, "error", f"unsupported_operational_db_driver: {driver}")
    dialect: MigrationDialect = "sqlite" if driver == "sqlite" else "postgres"
    try:
        status = get_operational_schema_status(_operational_db_target(settings), dialect=dialect)
    except OperationalMigrationError as exc:
        return StartupCheck(
            "operational_schema_version",
            False,
            "error",
            f"operational_schema_status_failed: {exc}",
        )

    if status.up_to_date:
        return StartupCheck(
            "operational_schema_version",
            True,
            "info",
            f"operational_schema_up_to_date version={status.current_version or 'none'}",
        )

    severity: StartupSeverity = "error" if settings.storage.operational_db_require_up_to_date else "warning"
    pending = ",".join(status.pending_versions) if status.pending_versions else "none"
    detail = (
        "operational_schema_mismatch "
        f"current={status.current_version or 'none'} "
        f"latest={status.latest_version} "
        f"pending={pending} "
        "run=db-upgrade"
    )
    return StartupCheck("operational_schema_version", False, severity, detail)


def _check_operational_db_config(settings: AppSettings) -> StartupCheck:
    driver = settings.storage.operational_db_driver.strip().lower() or "sqlite"
    if driver == "sqlite":
        path = settings.storage.operational_db_path.strip()
        if not path:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                "storage.operational_db.path_missing_for_sqlite",
            )
        return StartupCheck("operational_db_config", True, "info", "operational_db_sqlite_config_ok")
    if driver == "postgres":
        try:
            dsn = resolve_operational_db_dsn(settings)
        except Exception as exc:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                f"operational_db_dsn_resolution_failed: {exc}",
            )
        if not dsn:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                "storage.operational_db.dsn_missing_for_postgres",
            )
        if importlib.util.find_spec("psycopg") is None:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                "psycopg_not_installed_for_postgres_driver",
            )
        return StartupCheck("operational_db_config", True, "info", "operational_db_postgres_config_ok")
    return StartupCheck(
        "operational_db_config",
        False,
        "error",
        f"unsupported_operational_db_driver: {driver}",
    )


def _operational_db_target(settings: AppSettings) -> str:
    driver = settings.storage.operational_db_driver.strip().lower() or "sqlite"
    if driver == "sqlite":
        path = settings.storage.operational_db_path.strip()
        if not path:
            raise OperationalMigrationError("storage.operational_db.path is required for sqlite driver.")
        return path
    if driver == "postgres":
        try:
            dsn = resolve_operational_db_dsn(settings)
        except Exception as exc:
            raise OperationalMigrationError(f"failed to resolve postgres dsn from secrets provider: {exc}") from exc
        if not dsn:
            raise OperationalMigrationError("storage.operational_db.dsn is required for postgres driver.")
        return dsn
    raise OperationalMigrationError(f"Unsupported operational_db driver: {driver}")


def _check_persistence_read_write(persistence: JsonlPersistence) -> StartupCheck:
    try:
        persistence.artifacts_dir.mkdir(parents=True, exist_ok=True)
        persistence.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        _ = persistence.read_run_events("startup-probe")
        _ = persistence.read_all_artifact_records("startup-probe")
        return StartupCheck("jsonl_persistence", True, "info", "jsonl_persistence_access_ok")
    except Exception as exc:
        return StartupCheck("jsonl_persistence", False, "error", f"jsonl_persistence_failed: {exc}")


def _check_sandbox_chain_config(settings: AppSettings) -> tuple[StartupCheck, ...]:
    checks: list[StartupCheck] = []
    sandbox = settings.sandbox_chain

    if settings.execution.mode.value != "SANDBOX_CHAIN" and not sandbox.submit_tx:
        checks.append(StartupCheck("sandbox_chain_mode", True, "info", "sandbox_chain_not_primary_mode"))
        return tuple(checks)

    if sandbox.submit_tx:
        if not sandbox.enabled:
            checks.append(
                StartupCheck(
                    "sandbox_chain_enabled",
                    False,
                    "error",
                    "sandbox_chain.submit_tx=true requires sandbox_chain.enabled=true",
                )
            )
        else:
            checks.append(StartupCheck("sandbox_chain_enabled", True, "info", "sandbox_chain_enabled_ok"))
        if not sandbox.rpc_url.strip():
            checks.append(StartupCheck("sandbox_chain_rpc_url", False, "error", "sandbox_chain.rpc_url_missing"))
        else:
            checks.append(StartupCheck("sandbox_chain_rpc_url", True, "info", "sandbox_chain_rpc_url_ok"))
        if not sandbox.contract_address.strip():
            checks.append(
                StartupCheck("sandbox_chain_contract_address", False, "error", "sandbox_chain.contract_address_missing")
            )
        else:
            checks.append(StartupCheck("sandbox_chain_contract_address", True, "info", "sandbox_chain_contract_address_ok"))

        private_key = ""
        try:
            secrets = build_secret_provider(settings)
        except Exception as exc:
            checks.append(
                StartupCheck(
                    "sandbox_chain_secrets_provider",
                    False,
                    "error",
                    f"sandbox_chain_secret_provider_failed: {exc}",
                )
            )
            return tuple(checks)
        if sandbox.private_key_env.strip():
            private_key = secrets.get(sandbox.private_key_env.strip())
        if private_key:
            if importlib.util.find_spec("eth_account") is None:
                checks.append(
                    StartupCheck(
                        "sandbox_chain_signing_dependency",
                        False,
                        "error",
                        "eth_account_not_installed_for_private_key_signing",
                    )
                )
            else:
                checks.append(
                    StartupCheck(
                        "sandbox_chain_signing_dependency",
                        True,
                        "info",
                        "eth_account_dependency_available",
                    )
                )
        elif sandbox.allow_unlocked_send and sandbox.from_address.strip():
            checks.append(StartupCheck("sandbox_chain_unlocked_send", True, "warning", "using_unlocked_send_for_submission"))
        else:
            checks.append(
                StartupCheck(
                    "sandbox_chain_signing_path",
                    False,
                    "error",
                    "no_private_key_or_unlocked_sender_configured_for_submit_tx",
                )
            )
    else:
        checks.append(StartupCheck("sandbox_chain_submit_tx", True, "info", "sandbox_chain_submit_tx_disabled"))

    return tuple(checks)
