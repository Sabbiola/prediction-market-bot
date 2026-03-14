from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from logging import Logger
from pathlib import Path
from typing import Mapping, Sequence

from prediction_market_bot.app.performance import CommandProfiler
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.domain.enums import TradeReviewAction, TradeReviewStatus
from prediction_market_bot.domain.models import ExecutionResult, PendingSettlementRequest
from prediction_market_bot.infrastructure import JsonlPersistence, OperationalRepositories
from prediction_market_bot.services import (
    OperatorControlState,
    RuntimeMetricsSnapshot,
    StartupValidationReport,
    collect_runtime_metrics,
    list_run_ids,
    operator_state_path,
    validate_startup,
    write_prometheus_textfile,
)


def control_state_path(settings: AppSettings) -> Path:
    return operator_state_path(settings.storage.artifacts_dir)


def write_json_file(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def resolve_run_id(persistence: JsonlPersistence, state: OperatorControlState) -> str | None:
    if state.last_run_id.strip():
        return state.last_run_id.strip()
    run_ids = list_run_ids(persistence, limit_runs=1)
    if run_ids:
        return run_ids[-1]
    return None


def run_startup_validation(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: OperationalRepositories,
) -> StartupValidationReport:
    return validate_startup(
        settings=settings,
        persistence=persistence,
        operational=operational,
    )


def print_startup_report(report: StartupValidationReport) -> None:
    for check in report.checks:
        state = "OK" if check.ok else "FAIL"
        print(f"[{state}] {check.name}: {check.detail}")


def maybe_write_runtime_metrics(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: OperationalRepositories,
) -> RuntimeMetricsSnapshot | None:
    if not settings.metrics.enabled:
        return None
    if settings.metrics.exporter.strip().lower() != "prometheus_textfile":
        return None
    snapshot = collect_runtime_metrics(
        settings=settings,
        persistence=persistence,
        operational=operational,
    )
    write_prometheus_textfile(settings.metrics.path, snapshot)
    return snapshot


def execution_from_settlement_request(request: PendingSettlementRequest) -> ExecutionResult:
    return ExecutionResult(
        market_id=request.market_id,
        execution_mode=request.execution_mode,
        status=request.execution_status,
        side=request.side,
        stake_usd=request.stake_usd,
        fill_price=request.fill_price,
        order_id=request.order_id,
        message="execution_reconstructed_from_pending_settlement_request",
    )


def latest_payload_index(rows: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    index: dict[str, Mapping[str, object]] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        market_id = extract_market_id(payload)
        if not market_id:
            continue
        index[market_id] = payload
    return index


def extract_market_id(payload: Mapping[str, object], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    direct = payload.get("market_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = extract_market_id(value, depth=depth + 1)
            if nested:
                return nested
    return ""


def summary_is_empty(summary: object) -> bool:
    if not hasattr(summary, "event_count"):
        return True
    event_count = getattr(summary, "event_count")
    total_markets = getattr(summary, "total_markets", 0)
    candidates = getattr(summary, "candidates", 0)
    return int(event_count) == 0 and int(total_markets) == 0 and int(candidates) == 0


def parse_date_arg(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def parse_review_status(value: str) -> TradeReviewStatus | None:
    normalized = value.strip().upper()
    if normalized == "ALL":
        return None
    aliases = {
        "PENDING": "PENDING_REVIEW",
    }
    return TradeReviewStatus(aliases.get(normalized, normalized))


def parse_review_action(value: str) -> TradeReviewAction:
    return TradeReviewAction(value.strip().upper())


_ROLE_ORDER = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}


def require_cli_role(
    *,
    settings: AppSettings,
    required_role: str,
    action_name: str,
    acting_user: str = "",
    acting_role: str = "",
) -> tuple[str, str]:
    user = acting_user.strip()
    role = acting_role.strip().lower()
    if settings.security.cli_auth.enabled:
        if not user:
            user = os.getenv(settings.security.cli_auth.actor_user_env, "").strip()
        if not role:
            role = os.getenv(settings.security.cli_auth.actor_role_env, "").strip().lower()
        if not user or not role:
            raise PermissionError(
                "cli_auth_required set actor env vars "
                f"{settings.security.cli_auth.actor_user_env}/{settings.security.cli_auth.actor_role_env}"
            )
    if role:
        if _ROLE_ORDER.get(role, 0) < _ROLE_ORDER.get(required_role, 0):
            raise PermissionError(f"insufficient_role required={required_role} provided={role} action={action_name}")
    return user, role


def build_command_profiler(*, settings: AppSettings, profile: bool = False) -> CommandProfiler:
    enabled = bool(profile or settings.performance.enable_cli_profile)
    return CommandProfiler(enabled=enabled)


def emit_command_profile(
    *,
    profiler: CommandProfiler,
    logger: Logger,
    command: str,
    run_id: str = "",
) -> None:
    if not profiler.enabled:
        return
    payload = profiler.payload()
    logger.info(
        "command_profile_summary",
        extra={
            "event": "command_profile_summary",
            "command": command,
            "run_id": run_id,
            **payload,
        },
    )
    print(profiler.summary_text(command=command), file=sys.stderr)
