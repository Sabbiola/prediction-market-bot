from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_http_client, build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.secrets import build_secret_provider
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure import SandboxChainExecutor
from prediction_market_bot.services import AlertEvent, SandboxTransactionService, TxStatusSnapshot, build_alerting_service

from prediction_market_bot.cli.common import (
    maybe_write_runtime_metrics,
    print_startup_report,
    require_cli_role,
    run_startup_validation,
)


def _build_sandbox_tx_service(settings: AppSettings) -> SandboxTransactionService:
    operational = build_operational_repositories(settings)
    secrets = build_secret_provider(settings)
    http_client = build_http_client(settings, secrets=secrets)
    private_key = ""
    private_key_env = settings.sandbox_chain.private_key_env.strip()
    if private_key_env:
        private_key = secrets.get(private_key_env)
    executor = SandboxChainExecutor(
        rpc_url=settings.sandbox_chain.rpc_url,
        contract_address=settings.sandbox_chain.contract_address,
        chain_id=settings.sandbox_chain.chain_id,
        from_address=settings.sandbox_chain.from_address,
        intent_method_selector=settings.sandbox_chain.intent_method_selector,
        submit_tx=settings.sandbox_chain.submit_tx,
        private_key=private_key,
        allow_unlocked_send=settings.sandbox_chain.allow_unlocked_send,
        gas_limit=settings.sandbox_chain.gas_limit,
        confirmations_required=settings.sandbox_chain.confirmations_required,
        dropped_after_sec=settings.sandbox_chain.dropped_after_sec,
        timeout_sec=settings.sandbox_chain.request_timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        enabled=settings.sandbox_chain.enabled,
        http_client=http_client,
    )
    return SandboxTransactionService(
        executor=executor,
        intent_repo=operational.transaction_intents,
        attempt_repo=operational.transaction_attempts,
        receipt_repo=operational.transaction_receipts,
    )


def _render_tx_snapshots(rows: list[TxStatusSnapshot], *, as_json: bool) -> None:
    payload = [row.to_dict() for row in rows]
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"Transaction statuses: {len(rows)}")
    for row in rows:
        print(
            f"- intent_id={row.intent_id} run_id={row.run_id} market_id={row.market_id} "
            f"status={row.confirmation_status.value} tx_hash={row.latest_tx_hash or 'n/a'} "
            f"nonce={row.nonce if row.nonce is not None else 'n/a'} retries={row.retry_count}"
        )
        if row.review_queue_id:
            print(f"  review_queue_id={row.review_queue_id}")
        if row.message:
            print(f"  message={row.message}")


def tx_status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    intent_id: str | None,
    limit: int,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        return 1
    service = _build_sandbox_tx_service(settings)
    rows = service.list_status(run_id=run_id, intent_id=intent_id, limit=limit)
    _render_tx_snapshots(rows, as_json=as_json)
    maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def tx_reconcile_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    intent_id: str | None,
    limit: int,
    as_json: bool,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    alerting = build_alerting_service(settings)
    try:
        require_cli_role(
            settings=settings,
            required_role="operator",
            action_name="tx-reconcile",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"tx-reconcile blocked: {exc}")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        return 1
    service = _build_sandbox_tx_service(settings)
    try:
        rows = service.reconcile(run_id=run_id, intent_id=intent_id, limit=limit)
    except Exception as exc:
        alerting.emit(
            AlertEvent(
                event_type="tx_reconciliation_failure",
                severity="error",
                title="TX reconciliation failed",
                message="sandbox transaction reconciliation failed",
                details={
                    "run_id": (run_id or "").strip(),
                    "intent_id": (intent_id or "").strip(),
                    "error": str(exc),
                },
                dedupe_key=f"tx_reconciliation_failure:{(run_id or '').strip()}:{(intent_id or '').strip()}",
            )
        )
        print(f"tx-reconcile failed: {exc}")
        return 1
    _render_tx_snapshots(rows, as_json=as_json)
    maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def tx_resubmit_safe_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    intent_id: str,
    as_json: bool,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="tx-resubmit-safe",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"tx-resubmit-safe blocked: {exc}")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        return 1
    if not settings.sandbox_chain.submit_tx:
        print("tx-resubmit-safe requires sandbox_chain.submit_tx=true")
        return 2
    service = _build_sandbox_tx_service(settings)
    try:
        row = service.resubmit_safe(intent_id=intent_id.strip())
    except KeyError:
        print(f"Transaction intent not found: {intent_id}")
        return 1
    except ValueError as exc:
        print(f"Safe resubmit blocked: {exc}")
        return 2
    _render_tx_snapshots([row], as_json=as_json)
    maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0
