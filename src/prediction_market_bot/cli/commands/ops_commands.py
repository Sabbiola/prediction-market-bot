from __future__ import annotations

import json
import logging
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.domain.enums import SettlementRequestState, TradeReviewStatus
from prediction_market_bot.services import (
    PaperPortfolioEngine,
    SettlementRequestQueueService,
    TradeReviewQueueService,
    list_run_ids,
    load_operator_state,
    save_operator_state,
)

from prediction_market_bot.cli.common import control_state_path, maybe_write_runtime_metrics, require_cli_role

logger = logging.getLogger(__name__)


def status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(control_state_path(settings), repository=operational.operator_control_state)
    run_ids = list_run_ids(persistence, limit_runs=1000)
    latest_run_id = run_ids[-1] if run_ids else ""

    portfolio_rows = persistence.read_all_artifact_records("paper_portfolio_events")
    portfolio_engine = PaperPortfolioEngine(open_positions_repo=operational.open_positions)
    restored = portfolio_engine.restore_from_repository()
    if not restored:
        portfolio_engine.replay_rows(portfolio_rows)
    portfolio_snapshot = portfolio_engine.snapshot()
    review_queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    pending_review_count = len(review_queue.list_queue(status=TradeReviewStatus.PENDING_REVIEW, limit=0))
    settlement_queue = SettlementRequestQueueService(
        persistence,
        pending_repo=operational.pending_settlements,
    )
    pending_settlement_count = len(settlement_queue.list_requests(state=SettlementRequestState.PENDING, limit=0))
    runtime_metrics = maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)

    payload = {
        "runtime_mode": settings.runtime.mode.value,
        "market_data_provider": settings.runtime.market_data_provider.value,
        "research_provider": settings.runtime.research_provider.value,
        "provider_failure_policy": settings.runtime.provider_failure_policy.value,
        "execution_mode": settings.execution.mode.value,
        "rehearsal_mode": settings.execution.rehearsal_mode.value,
        "rehearsal_enabled": settings.execution.enable_rehearsal_lane,
        "review_blocking_gate": settings.effective_blocking_trade_review(),
        "manual_review_queue_enabled": settings.effective_manual_review_queue(),
        "review_auto_approve": settings.execution.review_auto_approve,
        "settlement_same_run": settings.execution.settlement_same_run,
        "live_market_data_enabled": settings.live_market_data.enabled,
        "live_research_enabled": settings.live_research.enabled,
        "paused": state.paused,
        "pause_reason": state.pause_reason,
        "last_run_id": state.last_run_id or latest_run_id,
        "last_run_status": state.last_run_status,
        "last_run_started_at": state.last_run_started_at,
        "last_run_finished_at": state.last_run_finished_at,
        "last_error": state.last_error,
        "known_run_count": len(run_ids),
        "pending_review_count": pending_review_count,
        "pending_settlement_count": pending_settlement_count,
        "runtime_metrics": runtime_metrics.to_dict() if runtime_metrics is not None else {},
        "portfolio": portfolio_snapshot.to_dict(),
        "last_report_markdown_path": state.last_report_markdown_path,
        "last_report_json_path": state.last_report_json_path,
        "scheduler_last_started_at": state.scheduler_last_started_at,
        "scheduler_last_tick_at": state.scheduler_last_tick_at,
        "scheduler_iterations": state.scheduler_iterations,
    }

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status_label = "PAUSED" if state.paused else "ACTIVE"
        print(f"System status: {status_label}")
        if state.paused and state.pause_reason:
            print(f"Pause reason: {state.pause_reason}")
        print(f"Last run: {payload['last_run_id'] or 'none'} ({state.last_run_status})")
        print(
            "Runtime: "
            f"mode={payload['runtime_mode']} "
            f"market_data={payload['market_data_provider']} "
            f"research={payload['research_provider']} "
            f"failure_policy={payload['provider_failure_policy']}"
        )
        print(
            "Execution: "
            f"mode={payload['execution_mode']} "
            f"rehearsal_mode={payload['rehearsal_mode']} "
            f"review_blocking={payload['review_blocking_gate']} "
            f"settlement_same_run={payload['settlement_same_run']}"
        )
        print(f"Known runs: {len(run_ids)}")
        print(f"Pending trade reviews: {pending_review_count}")
        print(f"Pending settlement requests: {pending_settlement_count}")
        print(
            "Portfolio: "
            f"positions={portfolio_snapshot.position_count} "
            f"exposure_usd={portfolio_snapshot.total_exposure_usd:.2f} "
            f"realized_pnl_usd={portfolio_snapshot.realized_pnl_usd:.2f} "
            f"unrealized_pnl_usd={portfolio_snapshot.unrealized_pnl_usd:.2f}"
        )
        if state.last_report_markdown_path:
            print(f"Last markdown report: {state.last_report_markdown_path}")
        if state.last_report_json_path:
            print(f"Last json report: {state.last_report_json_path}")

    logger.info(
        "operator_status",
        extra={
            "event": "operator_status",
            "paused": state.paused,
            "known_run_count": len(run_ids),
            "pending_review_count": pending_review_count,
            "pending_settlement_count": pending_settlement_count,
            "last_run_id": payload["last_run_id"],
            "position_count": portfolio_snapshot.position_count,
        },
    )
    return 0


def pause_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    reason: str,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="pause",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"pause blocked: {exc}")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.paused = True
    state.pause_reason = reason.strip()
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    persistence.write_run_event(
        "operator",
        "operator_pause",
        {
            "reason": state.pause_reason,
            "acting_user": actor_user,
            "acting_role": actor_role,
        },
    )
    print(f"Operator pause enabled. reason='{state.pause_reason or 'not_set'}'")
    logger.warning(
        "operator_paused",
        extra={
            "event": "operator_paused",
            "reason": state.pause_reason,
            "acting_user": actor_user,
            "acting_role": actor_role,
        },
    )
    return 0


def resume_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="resume",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"resume blocked: {exc}")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.paused = False
    state.pause_reason = ""
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    persistence.write_run_event(
        "operator",
        "operator_resume",
        {
            "acting_user": actor_user,
            "acting_role": actor_role,
        },
    )
    print("Operator pause cleared. System resumed.")
    logger.info(
        "operator_resumed",
        extra={
            "event": "operator_resumed",
            "acting_user": actor_user,
            "acting_role": actor_role,
        },
    )
    return 0
