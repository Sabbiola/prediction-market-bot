from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.infrastructure.operational_sqlite import SqliteOperationalRepositories

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
    operational: SqliteOperationalRepositories,
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
            _check_writable_dir(Path(settings.storage.operational_db_path).parent, name="operational_db_dir"),
        )
    )
    if settings.metrics.enabled:
        checks.append(_check_writable_dir(Path(settings.metrics.path).parent, name="metrics_dir"))

    checks.append(_check_operational_repositories(operational))
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


def _check_operational_repositories(operational: SqliteOperationalRepositories) -> StartupCheck:
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
        if sandbox.private_key_env.strip():
            private_key = os.getenv(sandbox.private_key_env.strip(), "").strip()
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
