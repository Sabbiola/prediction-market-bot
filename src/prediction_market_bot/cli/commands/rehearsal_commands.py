from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from fastapi.testclient import TestClient

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.secrets import resolve_operational_db_dsn
from prediction_market_bot.domain.enums import ExecutionMode, SettlementRequestState, TradeReviewStatus
from prediction_market_bot.infrastructure import verify_operational_sqlite_db
from prediction_market_bot.infrastructure.operational_migrations import (
    MigrationDialect,
    OperationalMigrationError,
    get_operational_schema_status,
    init_operational_schema,
    upgrade_operational_schema,
)
from prediction_market_bot.services import (
    PaperPortfolioEngine,
    SettlementRequestQueueService,
    TradeReviewQueueService,
)
from prediction_market_bot.ui.app import create_web_app

from prediction_market_bot.cli.commands.health_commands import healthcheck_command
from prediction_market_bot.cli.commands.report_commands import generate_report_command
from prediction_market_bot.cli.commands.replay_commands import replay_run_command
from prediction_market_bot.cli.commands.review_commands import review_approve_command
from prediction_market_bot.cli.commands.run_commands import (
    run_once_command,
    run_scheduler_command,
    settle_run_command,
    smoke_live_data_command,
    smoke_live_research_command,
)
from prediction_market_bot.cli.commands.tx_commands import tx_reconcile_command, tx_status_command
from prediction_market_bot.cli.common import run_startup_validation, write_json_file


@dataclass(slots=True, frozen=True)
class RehearsalStepResult:
    step: int
    key: str
    title: str
    ok: bool
    detail: str
    recovery_hint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "key": self.key,
            "title": self.title,
            "ok": self.ok,
            "detail": self.detail,
            "recovery_hint": self.recovery_hint,
        }


def _run_command_capture(func: Callable[..., int], /, *args: object, **kwargs: object) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = func(*args, **kwargs)
    return exit_code, buffer.getvalue().strip()


def _parse_json_object(raw: str) -> dict[str, object] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _parse_json_rows(raw: str) -> list[dict[str, object]]:
    text = raw.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    rows: list[dict[str, object]] = []
    for row in payload:
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _migration_dialect_from_settings(driver_value: str) -> MigrationDialect:
    driver = driver_value.strip().lower() or "sqlite"
    if driver == "sqlite":
        return "sqlite"
    if driver == "postgres":
        return "postgres"
    raise OperationalMigrationError(f"unsupported operational_db driver: {driver}")


def _migration_target_from_settings(*, dialect: MigrationDialect, settings_driver_path: str, settings_dsn: str) -> str:
    if dialect == "sqlite":
        target = settings_driver_path.strip()
        if not target:
            raise OperationalMigrationError("storage.operational_db.path is required for sqlite driver")
        return target
    target = settings_dsn.strip()
    if not target:
        raise OperationalMigrationError("storage.operational_db.dsn is required for postgres driver")
    return target


def beta_dress_rehearsal_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    operator_id: str,
    approval_rationale: str,
    ui_username: str,
    ui_password: str,
    report_output_path: Path | None,
    skip_scheduler_probe: bool,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    started_at = datetime.now(UTC)
    resolved_run_id = run_id.strip() if run_id and run_id.strip() else f"beta-dress-{started_at.strftime('%Y%m%d%H%M%S')}"
    report_path = report_output_path or Path(settings.storage.artifacts_dir) / "reports" / f"{resolved_run_id}.md"

    steps: list[RehearsalStepResult] = []

    def append_step(*, key: str, title: str, ok: bool, detail: str, recovery_hint: str) -> None:
        steps.append(
            RehearsalStepResult(
                step=len(steps) + 1,
                key=key,
                title=title,
                ok=ok,
                detail=detail,
                recovery_hint=recovery_hint,
            )
        )

    def refresh_runtime_handles() -> None:
        nonlocal settings, persistence, operational
        settings = load_settings(config_path, agents_config_path)
        persistence = build_persistence(settings)
        operational = build_operational_repositories(settings)

    # 1) database init/upgrade/verify
    try:
        dialect = _migration_dialect_from_settings(settings.storage.operational_db_driver)
        resolved_dsn = resolve_operational_db_dsn(settings) if dialect == "postgres" else ""
        target = _migration_target_from_settings(
            dialect=dialect,
            settings_driver_path=settings.storage.operational_db_path,
            settings_dsn=resolved_dsn,
        )
        init_status = init_operational_schema(target, dialect=dialect)
        upgraded_status = upgrade_operational_schema(target, dialect=dialect)
        schema_status = get_operational_schema_status(target, dialect=dialect)
        if dialect == "sqlite":
            verify_result = verify_operational_sqlite_db(settings.storage.operational_db_path)
            verify_ok = verify_result.ok
            verify_detail = (
                f"integrity_ok={verify_result.integrity_ok} "
                f"schema_up_to_date={verify_result.schema_up_to_date}"
            )
        else:
            verify_ok = schema_status.up_to_date
            verify_detail = f"schema_up_to_date={schema_status.up_to_date}"
        db_ok = schema_status.up_to_date and verify_ok
        db_detail = (
            f"dialect={dialect} init_current={init_status.current_version or 'none'} "
            f"upgraded_current={upgraded_status.current_version or 'none'} "
            f"latest={schema_status.latest_version} up_to_date={schema_status.up_to_date} "
            f"{verify_detail}"
        )
    except Exception as exc:
        db_ok = False
        db_detail = f"db_validation_failed error={exc}"
    append_step(
        key="db_init_upgrade_verify",
        title="DB init/upgrade/verify",
        ok=db_ok,
        detail=db_detail,
        recovery_hint=(
            "Run db-current-version/db-upgrade/db-verify and confirm DSN, migrations, and DB permissions."
        ),
    )

    # 2) startup validation
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    startup_failures = [check.name for check in startup_report.checks if not check.ok and check.severity == "error"]
    startup_ok = startup_report.ok
    append_step(
        key="startup_validation",
        title="Startup validation",
        ok=startup_ok,
        detail=(
            "startup_validation_ok"
            if startup_ok
            else f"startup_validation_failed checks={','.join(startup_failures) or 'unknown'}"
        ),
        recovery_hint="Run validate-startup --json and fix failing checks before retry.",
    )

    # 3) healthcheck
    health_exit, health_out = _run_command_capture(
        healthcheck_command,
        config_path,
        agents_config_path,
        as_json=True,
    )
    health_payload = _parse_json_object(health_out)
    health_status = str((health_payload or {}).get("status", "")).strip().lower()
    health_ok = health_exit == 0 and health_status == "ok"
    append_step(
        key="healthcheck",
        title="Healthcheck",
        ok=health_ok,
        detail=(
            f"status={health_status or 'unknown'} exit_code={health_exit}"
            if health_payload is not None
            else f"healthcheck_output_unparsed exit_code={health_exit}"
        ),
        recovery_hint="Run healthcheck --json, inspect failed checks/metrics, then rerun rehearsal.",
    )

    # 4) UI available and authenticated
    ui_ok = False
    ui_detail = "ui_probe_not_started"
    try:
        app = create_web_app(config_path=config_path, agents_config_path=agents_config_path)
        with TestClient(app) as client:
            health_res = client.get("/health")
            ready_res = client.get("/ready")
            if health_res.status_code != 200:
                ui_detail = f"ui_health_failed status_code={health_res.status_code}"
            elif ready_res.status_code != 200:
                ui_detail = f"ui_ready_failed status_code={ready_res.status_code}"
            elif not settings.ui_auth.enabled:
                ui_detail = "ui_auth_disabled_for_dress_rehearsal"
            elif not ui_username.strip() or not ui_password:
                ui_detail = "ui_auth_enabled_but_credentials_not_provided"
            else:
                login_res = client.post(
                    "/api/auth/login",
                    json={"username": ui_username.strip(), "password": ui_password},
                )
                login_payload = _parse_json_object(login_res.text)
                session_res = client.get("/api/auth/session")
                session_payload = _parse_json_object(session_res.text)
                authenticated = bool((login_payload or {}).get("authenticated")) and bool(
                    (session_payload or {}).get("authenticated")
                )
                if authenticated:
                    ui_ok = True
                    ui_detail = (
                        f"ui_ready_and_authenticated username={ui_username.strip()} "
                        f"role={str((session_payload or {}).get('role', 'unknown'))}"
                    )
                else:
                    ui_detail = (
                        f"ui_login_failed login_status={login_res.status_code} "
                        f"session_status={session_res.status_code}"
                    )
    except Exception as exc:
        ui_detail = f"ui_probe_failed error={exc}"
    append_step(
        key="ui_ready_auth",
        title="UI available + authenticated",
        ok=ui_ok,
        detail=ui_detail,
        recovery_hint=(
            "Ensure ui_auth.enabled=true, session secret/user credentials are configured, then verify /health,/ready and login."
        ),
    )

    # 5) worker/scheduler running
    if skip_scheduler_probe:
        scheduler_ok = True
        scheduler_detail = "scheduler_probe_skipped_by_flag"
    else:
        scheduler_exit, _ = _run_command_capture(
            run_scheduler_command,
            config_path,
            agents_config_path,
            interval_sec=0.0,
            max_iterations=1,
            run_id_prefix=f"{resolved_run_id}-sched",
            fail_fast=True,
        )
        scheduler_ok = scheduler_exit == 0
        scheduler_detail = f"scheduler_probe_exit_code={scheduler_exit} max_iterations=1"
    append_step(
        key="scheduler_probe",
        title="Worker/Scheduler running",
        ok=scheduler_ok,
        detail=scheduler_detail,
        recovery_hint="Run run-scheduler --max-iterations 1 and inspect startup, pause state, and runtime errors.",
    )

    # 6) live providers healthy
    provider_preconditions_ok = (
        settings.runtime.market_data_provider.value != "STATIC"
        and settings.runtime.research_provider.value != "STATIC"
        and settings.live_market_data.enabled
        and settings.live_research.enabled
    )
    if provider_preconditions_ok:
        market_smoke_exit, _ = _run_command_capture(
            smoke_live_data_command,
            config_path,
            agents_config_path,
            run_id=f"{resolved_run_id}-smoke-market",
            endpoint_url=settings.live_market_data.endpoint_url,
            timeout_sec=settings.live_market_data.timeout_sec,
            retries=settings.live_market_data.max_retries,
            retry_backoff_sec=settings.live_market_data.retry_backoff_sec,
            max_staleness_sec=settings.live_market_data.max_staleness_sec,
            limit=settings.live_market_data.limit,
        )
        research_smoke_exit, _ = _run_command_capture(
            smoke_live_research_command,
            config_path,
            agents_config_path,
            run_id=f"{resolved_run_id}-smoke-research",
            market_id=f"{resolved_run_id}-smoke-market",
            title="Sandbox-live dress rehearsal provider health probe",
            category="operations",
            query=None,
            limit_per_source=settings.live_research.limit_per_source,
            wikipedia_endpoint=settings.live_research.wikipedia_endpoint_url,
            openalex_endpoint=settings.live_research.openalex_endpoint_url,
            timeout_sec=settings.http.timeout_sec,
            retries=settings.http.max_retries,
            retry_backoff_sec=settings.http.retry_backoff_sec,
            cache_ttl_sec=settings.http.cache_ttl_sec,
        )
        providers_ok = market_smoke_exit == 0 and research_smoke_exit == 0
        providers_detail = f"smoke_live_data_exit={market_smoke_exit} smoke_live_research_exit={research_smoke_exit}"
    else:
        providers_ok = True
        providers_detail = (
            "live_providers_not_configured_skipped "
            f"market_data_provider={settings.runtime.market_data_provider.value} "
            f"research_provider={settings.runtime.research_provider.value} "
            f"live_market_data.enabled={settings.live_market_data.enabled} "
            f"live_research.enabled={settings.live_research.enabled}"
        )
    append_step(
        key="live_providers_health",
        title="Live providers healthy",
        ok=providers_ok,
        detail=providers_detail,
        recovery_hint="Run smoke-live-data/smoke-live-research and fix endpoint, auth, or provider-mode misconfiguration.",
    )

    # 7) run-once creates candidate(s)
    queue_service = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    run_once_first_exit, _ = _run_command_capture(
        run_once_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        force=False,
        profile=False,
    )
    pending_items = queue_service.list_queue(
        run_id=resolved_run_id,
        status=TradeReviewStatus.PENDING_REVIEW,
        limit=0,
    )
    first_queue_id = pending_items[0].queue_id if pending_items else ""
    run_once_review_ok = run_once_first_exit == 0 and bool(first_queue_id)
    append_step(
        key="run_once_review_candidate",
        title="Run-once creates review candidate",
        ok=run_once_review_ok,
        detail=(
            f"run_once_exit={run_once_first_exit} pending_review_count={len(pending_items)} "
            f"queue_id={first_queue_id or 'none'}"
        ),
        recovery_hint=(
            "Check blocking review gate settings and scanner/prediction/risk thresholds; run review-list --status pending_review."
        ),
    )

    # 8) review approval path
    if first_queue_id:
        approve_exit, _ = _run_command_capture(
            review_approve_command,
            config_path,
            agents_config_path,
            queue_id=first_queue_id,
            operator_id=operator_id,
            rationale=approval_rationale,
            note="beta_dress_rehearsal",
            acting_user=operator_id,
            acting_role="operator",
        )
        approved_item = queue_service.get_item(first_queue_id)
        approval_ok = approve_exit == 0 and approved_item is not None and approved_item.status == TradeReviewStatus.APPROVED
        approval_detail = (
            f"approve_exit={approve_exit} status={(approved_item.status.value if approved_item else 'missing')}"
        )
    else:
        approval_ok = False
        approval_detail = "approval_blocked_missing_queue_id"
    append_step(
        key="review_approval_path",
        title="Review approval path works",
        ok=approval_ok,
        detail=approval_detail,
        recovery_hint="Use review-show/review-approve with rationale and verify status transitions to APPROVED.",
    )

    # 9) sandbox tx submit/reconcile
    second_run_exit, _ = _run_command_capture(
        run_once_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        force=False,
        profile=False,
    )
    tx_status_exit, tx_status_out = _run_command_capture(
        tx_status_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        intent_id=None,
        limit=100,
        as_json=True,
    )
    tx_rows = _parse_json_rows(tx_status_out)
    has_submitted_hash = any(str(row.get("latest_tx_hash", "")).strip() for row in tx_rows)
    reconcile_exit, reconcile_out = _run_command_capture(
        tx_reconcile_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        intent_id=None,
        limit=100,
        as_json=True,
        acting_user=operator_id,
        acting_role="operator",
    )
    reconcile_rows = _parse_json_rows(reconcile_out)
    mined_count = sum(1 for row in reconcile_rows if str(row.get("confirmation_status", "")).upper() == "MINED")
    tx_ok = (
        second_run_exit == 0
        and tx_status_exit == 0
        and reconcile_exit == 0
        and has_submitted_hash
        and mined_count > 0
    )
    append_step(
        key="sandbox_tx_submit_reconcile",
        title="Sandbox tx submit/reconcile",
        ok=tx_ok,
        detail=(
            f"second_run_exit={second_run_exit} tx_status_exit={tx_status_exit} "
            f"reconcile_exit={reconcile_exit} tx_rows={len(tx_rows)} mined_count={mined_count}"
        ),
        recovery_hint="Inspect tx-status/tx-reconcile output, signer config, rpc health, and review-to-intent linkage.",
    )

    # Refresh runtime handles so steps 10-11 read back from the same storage targets used by step 9 sub-commands.
    refresh_runtime_handles()
    execution_records = persistence.read_artifact_records(resolved_run_id, "execution_results")
    filled_execution_count = 0
    for row in execution_records:
        execution_payload = row.get("payload")
        if not isinstance(execution_payload, dict):
            continue
        status = str(execution_payload.get("status", "")).strip().upper()
        if status == "FILLED":
            filled_execution_count += 1
    sandbox_submitted_only_ok = (
        settings.execution.mode == ExecutionMode.SANDBOX_CHAIN
        and tx_ok
        and mined_count > 0
        and filled_execution_count == 0
    )

    # 10) open position visible
    portfolio_engine = PaperPortfolioEngine(persistence=persistence, open_positions_repo=operational.open_positions)
    restored = portfolio_engine.restore_from_repository()
    if not restored:
        portfolio_engine.replay_rows(persistence.read_all_artifact_records("paper_portfolio_events"))
    snapshot = portfolio_engine.snapshot()
    positions_ok = snapshot.position_count > 0 or sandbox_submitted_only_ok
    positions_detail = (
        f"open_positions={snapshot.position_count} "
        f"exposure_usd={snapshot.total_exposure_usd:.2f} "
        f"unrealized_pnl_usd={snapshot.unrealized_pnl_usd:.2f} "
        f"filled_execution_results={filled_execution_count}"
    )
    if sandbox_submitted_only_ok and snapshot.position_count == 0:
        positions_detail = (
            f"{positions_detail} "
            f"open_position_check_skipped_sandbox_submitted_only mined_count={mined_count}"
        )
    append_step(
        key="open_position_visible",
        title="Open position visible",
        ok=positions_ok,
        detail=positions_detail,
        recovery_hint="Check execution results and paper portfolio events; confirm approved trade reached execution.",
    )

    # 11) settlement lane works
    settlement_exit, _ = _run_command_capture(
        settle_run_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
    )
    refresh_runtime_handles()
    settlement_queue = SettlementRequestQueueService(persistence, pending_repo=operational.pending_settlements)
    pending_settlements = settlement_queue.list_requests(
        run_id=resolved_run_id,
        state=SettlementRequestState.PENDING,
        limit=0,
    )
    settled_settlements = settlement_queue.list_requests(
        run_id=resolved_run_id,
        state=SettlementRequestState.SETTLED,
        limit=0,
    )
    settlement_total = len(pending_settlements) + len(settled_settlements)
    settlement_ok = settlement_exit == 0 and (settlement_total > 0 or sandbox_submitted_only_ok)
    settlement_detail = (
        f"settlement_exit={settlement_exit} "
        f"pending={len(pending_settlements)} settled={len(settled_settlements)} "
        f"filled_execution_results={filled_execution_count}"
    )
    if sandbox_submitted_only_ok and settlement_total == 0:
        settlement_detail = (
            f"{settlement_detail} "
            f"settlement_check_skipped_sandbox_submitted_only mined_count={mined_count}"
        )
    append_step(
        key="settlement_lane",
        title="Settlement lane works",
        ok=settlement_ok,
        detail=settlement_detail,
        recovery_hint="Run run-settlement-lane and inspect resolution checks + pending_settlements state transitions.",
    )

    # 12) replay/report generation works
    replay_exit, _ = _run_command_capture(
        replay_run_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        include_records=False,
        profile=False,
    )
    report_exit, _ = _run_command_capture(
        generate_report_command,
        config_path,
        agents_config_path,
        run_id=resolved_run_id,
        output_path=report_path,
        profile=False,
    )
    report_ok = replay_exit == 0 and report_exit == 0 and report_path.exists()
    append_step(
        key="replay_report",
        title="Replay/report generation works",
        ok=report_ok,
        detail=f"replay_exit={replay_exit} report_exit={report_exit} report_path={report_path}",
        recovery_hint="Run replay-run and generate-report manually; verify artifact presence under data/artifacts/reports.",
    )

    # 13) UI reflects lifecycle
    lifecycle_ok = False
    lifecycle_detail = "ui_lifecycle_probe_not_started"
    try:
        app = create_web_app(config_path=config_path, agents_config_path=agents_config_path)
        with TestClient(app) as client:
            if settings.ui_auth.enabled:
                if not ui_username.strip() or not ui_password:
                    lifecycle_detail = "ui_lifecycle_auth_blocked_missing_credentials"
                else:
                    login = client.post(
                        "/api/auth/login",
                        json={"username": ui_username.strip(), "password": ui_password},
                    )
                    login_payload = _parse_json_object(login.text) or {}
                    if not bool(login_payload.get("authenticated")):
                        lifecycle_detail = f"ui_lifecycle_login_failed status_code={login.status_code}"
                    else:
                        overview = client.get(f"/api/tabs/overview?run_id={resolved_run_id}")
                        review_queue = client.get(f"/api/tabs/review-queue?run_id={resolved_run_id}&status=APPROVED")
                        sandbox_tx = client.get(f"/api/tabs/sandbox-tx?run_id={resolved_run_id}")
                        positions = client.get(f"/api/tabs/positions?run_id={resolved_run_id}")
                        settlement = client.get(f"/api/tabs/settlement?run_id={resolved_run_id}")
                        reports = client.get(f"/api/tabs/reports?run_id={resolved_run_id}")
                        statuses = [
                            overview.status_code,
                            review_queue.status_code,
                            sandbox_tx.status_code,
                            positions.status_code,
                            settlement.status_code,
                            reports.status_code,
                        ]
                        review_payload = _parse_json_object(review_queue.text) or {}
                        tx_payload = _parse_json_object(sandbox_tx.text) or {}
                        report_payload = _parse_json_object(reports.text) or {}
                        rows = review_payload.get("rows")
                        review_rows_count = len(rows) if isinstance(rows, list) else 0
                        attempts_raw = tx_payload.get("attempts_count", 0)
                        if isinstance(attempts_raw, bool):
                            attempts_count = 0
                        elif isinstance(attempts_raw, (int, float)):
                            attempts_count = int(attempts_raw)
                        elif isinstance(attempts_raw, str):
                            try:
                                attempts_count = int(float(attempts_raw.strip() or "0"))
                            except ValueError:
                                attempts_count = 0
                        else:
                            attempts_count = 0
                        report_run_id = str(report_payload.get("run_id", "")).strip()
                        lifecycle_ok = all(code == 200 for code in statuses) and (
                            review_rows_count > 0 or attempts_count > 0
                        ) and report_run_id == resolved_run_id
                        lifecycle_detail = (
                            f"tab_statuses={statuses} review_rows={review_rows_count} "
                            f"sandbox_attempts={attempts_count} reports_run_id={report_run_id or 'missing'}"
                        )
            else:
                lifecycle_detail = "ui_lifecycle_probe_requires_ui_auth_enabled"
    except Exception as exc:
        lifecycle_detail = f"ui_lifecycle_probe_failed error={exc}"
    append_step(
        key="ui_lifecycle_reflection",
        title="UI reflects lifecycle",
        ok=lifecycle_ok,
        detail=lifecycle_detail,
        recovery_hint="Verify tab payloads (/overview,/review-queue,/sandbox-tx,/positions,/settlement,/reports) for the rehearsal run_id.",
    )

    ok = all(step.ok for step in steps)
    finished_at = datetime.now(UTC)
    result: dict[str, object] = {
        "ok": ok,
        "go_no_go": "GO" if ok else "NO_GO",
        "run_id": resolved_run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round((finished_at - started_at).total_seconds(), 3),
        "steps": [step.to_dict() for step in steps],
        "failed_steps": [step.key for step in steps if not step.ok],
        "report_output_path": str(report_path),
    }
    evidence_path = Path(settings.storage.artifacts_dir) / "rehearsals" / f"{resolved_run_id}.json"
    write_json_file(evidence_path, result)
    result["evidence_path"] = str(evidence_path)

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Sandbox-live dress rehearsal run_id={resolved_run_id}")
        print(f"Go/No-Go: {result['go_no_go']}")
        for step in steps:
            status = "OK" if step.ok else "FAIL"
            print(f"{step.step:02d}. [{status}] {step.title}")
            print(f"    detail: {step.detail}")
            if not step.ok:
                print(f"    recovery: {step.recovery_hint}")
        print(f"Report output: {report_path}")
        print(f"Evidence output: {evidence_path}")

    return 0 if ok else 1
