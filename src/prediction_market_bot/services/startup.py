from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prediction_market_bot.app.secrets import build_secret_provider, resolve_operational_db_dsn
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure.alt_data import AltDataAdapterRegistry
from prediction_market_bot.infrastructure.operational_migrations import (
    MigrationDialect,
    OperationalMigrationError,
    get_operational_schema_status,
)
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.infrastructure.operational_sqlite import OperationalRepositories
from prediction_market_bot.services.model_promotion import resolve_runtime_model_gate_decision
from prediction_market_bot.services.operator_control import load_operator_state, operator_state_path

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

    checks.extend(_check_staging_profile_requirements(settings))

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
    checks.append(_check_model_promotion_gate(settings=settings, operational=operational))
    checks.append(_check_persistence_read_write(persistence))
    checks.extend(_check_alt_data_configuration(settings))
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
                (
                    "operational_db_dsn_resolution_failed "
                    f"driver=postgres dsn_env={settings.storage.operational_db_dsn_env or 'unset'} error={exc} "
                    "fix=set storage.operational_db.dsn or export the configured dsn_env value"
                ),
            )
        if not dsn:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                (
                    "storage.operational_db.dsn_missing_for_postgres "
                    "fix=set storage.operational_db.dsn or export OPERATIONAL_DB_DSN"
                ),
            )
        if importlib.util.find_spec("psycopg") is None:
            return StartupCheck(
                "operational_db_config",
                False,
                "error",
                "psycopg_not_installed_for_postgres_driver fix=pip install \".[postgres]\"",
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


def _check_model_promotion_gate(*, settings: AppSettings, operational: OperationalRepositories) -> StartupCheck:
    try:
        state = load_operator_state(
            operator_state_path(settings.storage.artifacts_dir),
            repository=operational.operator_control_state,
        )
    except Exception as exc:
        return StartupCheck(
            "model_promotion_gate",
            False,
            "warning",
            f"model_promotion_state_unavailable: {exc}",
        )
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    if decision.allowed:
        return StartupCheck(
            "model_promotion_gate",
            True,
            "info",
            (
                "model_gate_ok "
                f"requested={decision.requested_engine} effective={decision.effective_engine} "
                f"reason={decision.reason}"
            ),
        )

    if decision.effective_engine == "heuristic":
        severity: StartupSeverity = "warning"
    else:
        severity = "error"
    return StartupCheck(
        "model_promotion_gate",
        False,
        severity,
        (
            "model_gate_blocked "
            f"requested={decision.requested_engine} effective={decision.effective_engine} reason={decision.reason} "
            f"model_version={decision.model_version or 'unset'} promoted_version={decision.promoted_version or 'unset'}"
        ),
    )


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
            checks.append(
                StartupCheck(
                    "sandbox_chain_rpc_url",
                    False,
                    "error",
                    "sandbox_chain.rpc_url_missing fix=set sandbox_chain.rpc_url",
                )
            )
        else:
            checks.append(StartupCheck("sandbox_chain_rpc_url", True, "info", "sandbox_chain_rpc_url_ok"))
        if not sandbox.contract_address.strip():
            checks.append(
                StartupCheck(
                    "sandbox_chain_contract_address",
                    False,
                    "error",
                    "sandbox_chain.contract_address_missing fix=set sandbox_chain.contract_address",
                )
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
                    (
                        "no_private_key_or_unlocked_sender_configured_for_submit_tx "
                        "fix=provide sandbox private key env or enable allow_unlocked_send with from_address"
                    ),
                )
            )
    else:
        checks.append(StartupCheck("sandbox_chain_submit_tx", True, "info", "sandbox_chain_submit_tx_disabled"))

    return tuple(checks)


def _check_alt_data_configuration(settings: AppSettings) -> tuple[StartupCheck, ...]:
    if not settings.alt_data.enabled:
        return (StartupCheck("alt_data_sources", True, "info", "alt_data_disabled"),)

    try:
        secrets = build_secret_provider(settings)
    except Exception as exc:
        return (
            StartupCheck(
                "alt_data_sources",
                False,
                "error",
                f"alt_data_secret_provider_failed: {exc}",
            ),
        )

    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=lambda env_name: secrets.get(env_name),
    )
    issues = registry.validate_enabled_sources()
    if not issues:
        enabled = ",".join(registry.list_enabled_ids()) or "none"
        return (StartupCheck("alt_data_sources", True, "info", f"alt_data_sources_ready enabled={enabled}"),)
    details = " | ".join(issue.detail for issue in issues)
    return (
        StartupCheck(
            "alt_data_sources",
            False,
            "error",
            f"alt_data_sources_invalid: {details}",
        ),
    )


def _check_staging_profile_requirements(settings: AppSettings) -> tuple[StartupCheck, ...]:
    env = settings.runtime.env.strip().lower()
    if env not in {"staging", "beta-live-staging", "sandbox-live-staging"}:
        return (StartupCheck("staging_profile_mode", True, "info", f"staging_profile_not_required env={env or 'unset'}"),)

    checks: list[StartupCheck] = []
    if settings.runtime.mode.value != "SANDBOX_CHAIN":
        checks.append(
            StartupCheck(
                "staging_profile_runtime_mode",
                False,
                "error",
                (
                    "staging_profile_requires_runtime_mode_SANDBOX_CHAIN "
                    f"configured={settings.runtime.mode.value}"
                ),
            )
        )
    else:
        checks.append(StartupCheck("staging_profile_runtime_mode", True, "info", "staging_runtime_mode_ok"))

    if settings.execution.mode.value != "SANDBOX_CHAIN":
        checks.append(
            StartupCheck(
                "staging_profile_execution_mode",
                False,
                "error",
                (
                    "staging_profile_requires_execution_mode_SANDBOX_CHAIN "
                    f"configured={settings.execution.mode.value}"
                ),
            )
        )
    else:
        checks.append(StartupCheck("staging_profile_execution_mode", True, "info", "staging_execution_mode_ok"))

    live_market_provider_ok = settings.runtime.market_data_provider.value != "STATIC"
    live_research_provider_ok = settings.runtime.research_provider.value != "STATIC"
    if (
        settings.live_market_data.enabled
        and settings.live_research.enabled
        and live_market_provider_ok
        and live_research_provider_ok
    ):
        checks.append(StartupCheck("staging_profile_live_providers", True, "info", "staging_live_providers_enabled"))
    else:
        checks.append(
            StartupCheck(
                "staging_profile_live_providers",
                False,
                "error",
                (
                    "staging_profile_requires_live_providers "
                    f"live_market_data.enabled={settings.live_market_data.enabled} "
                    f"live_research.enabled={settings.live_research.enabled} "
                    f"market_data_provider={settings.runtime.market_data_provider.value} "
                    f"research_provider={settings.runtime.research_provider.value}"
                ),
            )
        )

    if settings.effective_blocking_trade_review() and not settings.execution.review_auto_approve:
        checks.append(StartupCheck("staging_profile_review_gate", True, "info", "staging_review_gate_enabled"))
    else:
        checks.append(
            StartupCheck(
                "staging_profile_review_gate",
                False,
                "error",
                (
                    "staging_profile_requires_blocking_review_and_no_auto_approve "
                    f"effective_blocking={settings.effective_blocking_trade_review()} "
                    f"review_auto_approve={settings.execution.review_auto_approve}"
                ),
            )
        )

    if settings.sandbox_chain.enabled and settings.sandbox_chain.submit_tx:
        checks.append(StartupCheck("staging_profile_sandbox_tx", True, "info", "staging_sandbox_tx_enabled"))
    else:
        checks.append(
            StartupCheck(
                "staging_profile_sandbox_tx",
                False,
                "error",
                (
                    "staging_profile_requires_sandbox_tx_enabled "
                    f"sandbox_chain.enabled={settings.sandbox_chain.enabled} "
                    f"sandbox_chain.submit_tx={settings.sandbox_chain.submit_tx}"
                ),
            )
        )

    if not settings.execution.settlement_same_run:
        checks.append(
            StartupCheck("staging_profile_settlement_lane", True, "info", "staging_settlement_same_run_disabled")
        )
    else:
        checks.append(
            StartupCheck(
                "staging_profile_settlement_lane",
                False,
                "error",
                "staging_profile_requires_settlement_same_run_false",
            )
        )

    if settings.ui_auth.enabled and settings.ui_auth.require_password_hashes:
        checks.append(StartupCheck("staging_profile_ui_auth", True, "info", "staging_ui_auth_enabled"))
    else:
        checks.append(
            StartupCheck(
                "staging_profile_ui_auth",
                False,
                "error",
                (
                    "staging_profile_requires_ui_auth_enabled_with_password_hashes "
                    f"ui_auth.enabled={settings.ui_auth.enabled} "
                    f"ui_auth.require_password_hashes={settings.ui_auth.require_password_hashes}"
                ),
            )
        )

    if (
        settings.storage.operational_db_driver.strip().lower() == "postgres"
        and settings.storage.operational_db_require_up_to_date
        and (
            settings.storage.operational_db_dsn.strip()
            or settings.storage.operational_db_dsn_env.strip()
        )
    ):
        checks.append(StartupCheck("staging_profile_operational_db", True, "info", "staging_operational_db_profile_ok"))
    else:
        checks.append(
            StartupCheck(
                "staging_profile_operational_db",
                False,
                "error",
                (
                    "staging_profile_requires_shared_postgres_operational_db "
                    f"driver={settings.storage.operational_db_driver} "
                    f"require_up_to_date={settings.storage.operational_db_require_up_to_date} "
                    f"dsn_set={bool(settings.storage.operational_db_dsn.strip())} "
                    f"dsn_env_set={bool(settings.storage.operational_db_dsn_env.strip())}"
                ),
            )
        )

    return tuple(checks)
