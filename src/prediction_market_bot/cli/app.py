from __future__ import annotations

from typing import Sequence

from prediction_market_bot.cli.commands.db_commands import (
    db_backup_command,
    db_current_version_command,
    db_init_command,
    db_restore_command,
    db_upgrade_command,
    db_verify_command,
)
from prediction_market_bot.cli.commands.health_commands import healthcheck_command, validate_startup_command
from prediction_market_bot.cli.commands.ops_commands import pause_command, resume_command, status_command
from prediction_market_bot.cli.commands.report_commands import (
    generate_report_command,
    last_report_command,
    paper_portfolio_state_command,
)
from prediction_market_bot.cli.commands.replay_commands import (
    evaluate_window_command,
    generate_eval_report_command,
    replay_run_command,
)
from prediction_market_bot.cli.commands.review_commands import (
    review_action_command,
    review_approve_command,
    review_list_command,
    review_reject_command,
    review_show_command,
)
from prediction_market_bot.cli.commands.run_commands import (
    run_once_command,
    run_scheduler_command,
    settle_run_command,
    smoke_live_data_command,
    smoke_live_research_command,
)
from prediction_market_bot.cli.commands.tx_commands import (
    tx_reconcile_command,
    tx_resubmit_safe_command,
    tx_status_command,
)
from prediction_market_bot.cli.common import parse_date_arg
from prediction_market_bot.cli.parser import build_parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command in {"run-once", "run"}:
        return run_once_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            force=args.force,
            profile=args.profile,
        )
    if args.command in {"settle-run", "run-settlement-lane"}:
        return settle_run_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
        )
    if args.command == "db-init":
        return db_init_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "db-upgrade":
        return db_upgrade_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "db-current-version":
        return db_current_version_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "db-backup":
        return db_backup_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            backup_dir=args.backup_dir,
            label=args.label,
            as_json=args.json,
        )
    if args.command == "db-restore":
        return db_restore_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            backup_file=args.backup_file,
            backup_dir=args.backup_dir,
            force=args.force,
            as_json=args.json,
        )
    if args.command == "db-verify":
        return db_verify_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "status":
        return status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "healthcheck":
        return healthcheck_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "validate-startup":
        return validate_startup_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "run-scheduler":
        return run_scheduler_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            interval_sec=args.interval_sec,
            max_iterations=args.max_iterations,
            run_id_prefix=args.run_id_prefix,
            fail_fast=args.fail_fast,
        )
    if args.command == "pause":
        return pause_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            reason=args.reason,
        )
    if args.command == "resume":
        return resume_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
        )
    if args.command in {"review-list", "review-queue"}:
        return review_list_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            status=args.status,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "review-show":
        return review_show_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            as_json=args.json,
        )
    if args.command == "review-approve":
        return review_approve_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command == "review-reject":
        return review_reject_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command == "review-action":
        return review_action_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            action=args.action,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command in {"replay-run", "replay"}:
        return replay_run_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            include_records=args.include_records,
            profile=args.profile,
        )
    if args.command == "evaluate-window":
        return evaluate_window_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            date_from=parse_date_arg(args.date_from),
            date_to=parse_date_arg(args.date_to),
            limit_runs=args.limit_runs,
            profile=args.profile,
        )
    if args.command == "generate-eval-report":
        date_from = parse_date_arg(args.date_from)
        date_to = parse_date_arg(args.date_to)
        if args.run_id is None and date_from is None and date_to is None:
            parser.error("generate-eval-report requires --run-id or at least one of --date-from/--date-to.")
        return generate_eval_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            date_from=date_from,
            date_to=date_to,
            limit_runs=args.limit_runs,
            output_path=args.output,
            profile=args.profile,
        )
    if args.command == "generate-report":
        return generate_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            output_path=args.output,
            profile=args.profile,
        )
    if args.command == "smoke-live-data":
        return smoke_live_data_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            endpoint_url=args.endpoint,
            timeout_sec=args.timeout_sec,
            retries=args.retries,
            retry_backoff_sec=args.retry_backoff_sec,
            max_staleness_sec=args.max_staleness_sec,
            limit=args.limit,
        )
    if args.command == "smoke-live-research":
        return smoke_live_research_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            market_id=args.market_id,
            title=args.title,
            category=args.category,
            query=args.query,
            limit_per_source=args.limit_per_source,
            wikipedia_endpoint=args.wikipedia_endpoint,
            openalex_endpoint=args.openalex_endpoint,
            timeout_sec=args.timeout_sec,
            retries=args.retries,
            retry_backoff_sec=args.retry_backoff_sec,
            cache_ttl_sec=args.cache_ttl_sec,
        )
    if args.command in {"paper-portfolio-state", "portfolio"}:
        return paper_portfolio_state_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
        )
    if args.command == "last-report":
        return last_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            report_format=args.format,
            path_only=args.path_only,
        )
    if args.command == "tx-status":
        return tx_status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            intent_id=args.intent_id,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "tx-reconcile":
        return tx_reconcile_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            intent_id=args.intent_id,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "tx-resubmit-safe":
        return tx_resubmit_safe_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            intent_id=args.intent_id,
            as_json=args.json,
        )

    parser.error("Unknown command.")
    return 2
