from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prediction-market-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once = subparsers.add_parser("run-once", help="Run one complete paper-live cycle (no real order posting).")
    run_once.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_once.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_once.add_argument("--run-id", default=None, help="Optional explicit run id for deterministic replay.")
    run_once.add_argument(
        "--force",
        action="store_true",
        help="Run even when operator pause is active (intended for controlled CI operations).",
    )
    run_once.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    run_alias = subparsers.add_parser("run", help="Alias of run-once.")
    run_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_alias.add_argument("--run-id", default=None, help="Optional explicit run id for deterministic replay.")
    run_alias.add_argument(
        "--force",
        action="store_true",
        help="Run even when operator pause is active (intended for controlled CI operations).",
    )
    run_alias.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    settle_run = subparsers.add_parser(
        "settle-run",
        help="Run settlement lane for pending settlement requests of a run.",
    )
    settle_run.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    settle_run.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    settle_run.add_argument(
        "--run-id",
        default=None,
        help="Run id to settle. Defaults to latest known run.",
    )

    settlement_lane = subparsers.add_parser("run-settlement-lane", help="Alias of settle-run.")
    settlement_lane.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    settlement_lane.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    settlement_lane.add_argument(
        "--run-id",
        default=None,
        help="Run id to settle. Defaults to latest known run.",
    )

    db_init = subparsers.add_parser(
        "db-init",
        help="Initialize operational DB schema using migration framework.",
    )
    db_init.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_init.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    db_init.add_argument("--json", action="store_true", help="Print migration payload as JSON.")

    db_upgrade = subparsers.add_parser(
        "db-upgrade",
        help="Apply pending operational DB migrations.",
    )
    db_upgrade.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_upgrade.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    db_upgrade.add_argument("--json", action="store_true", help="Print migration payload as JSON.")

    db_current = subparsers.add_parser(
        "db-current-version",
        help="Show operational DB schema migration version status.",
    )
    db_current.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_current.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    db_current.add_argument("--json", action="store_true", help="Print migration status payload as JSON.")

    db_backup = subparsers.add_parser(
        "db-backup",
        help="Create an immutable timestamped SQLite backup snapshot for operational DB.",
    )
    db_backup.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_backup.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    db_backup.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Optional backup directory override. Defaults to storage.operational_db.backup_dir.",
    )
    db_backup.add_argument("--label", default="", help="Optional filename label suffix.")
    db_backup.add_argument("--json", action="store_true", help="Print backup payload as JSON.")

    db_restore = subparsers.add_parser(
        "db-restore",
        help="Restore operational SQLite DB from a backup snapshot.",
    )
    db_restore.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_restore.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    source_group = db_restore.add_mutually_exclusive_group()
    source_group.add_argument(
        "--backup-file",
        type=Path,
        default=None,
        help="Backup file to restore from. Defaults to latest backup in backup dir.",
    )
    source_group.add_argument(
        "--latest",
        action="store_true",
        help="Restore from latest backup in backup dir (default behavior when no --backup-file is passed).",
    )
    db_restore.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Optional backup directory override used to resolve --latest/default source.",
    )
    db_restore.add_argument(
        "--force",
        action="store_true",
        help="Allow overwrite when target operational DB already exists.",
    )
    db_restore.add_argument("--json", action="store_true", help="Print restore + verify payload as JSON.")

    db_verify = subparsers.add_parser(
        "db-verify",
        help="Verify operational SQLite DB integrity and migration schema status.",
    )
    db_verify.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    db_verify.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    db_verify.add_argument("--json", action="store_true", help="Print verification payload as JSON.")

    status = subparsers.add_parser("status", help="Show operator-oriented system status.")
    status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    status.add_argument("--json", action="store_true", help="Print status payload as JSON.")

    healthcheck = subparsers.add_parser(
        "healthcheck",
        help="Run startup/dependency/config checks and emit staging health payload.",
    )
    healthcheck.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    healthcheck.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    healthcheck.add_argument("--json", action="store_true", help="Print health payload as JSON.")

    validate_startup = subparsers.add_parser(
        "validate-startup",
        help="Validate runtime dependencies and configuration without running a trading cycle.",
    )
    validate_startup.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    validate_startup.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    validate_startup.add_argument("--json", action="store_true", help="Print validation payload as JSON.")

    scheduler = subparsers.add_parser("run-scheduler", help="Run dry-run scheduler loop from terminal/CI.")
    scheduler.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    scheduler.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    scheduler.add_argument("--interval-sec", type=float, default=60.0, help="Seconds between scheduler ticks.")
    scheduler.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N iterations. 0 means run until interrupted.",
    )
    scheduler.add_argument("--run-id-prefix", default="sched", help="Prefix used to generate scheduler run ids.")
    scheduler.add_argument("--fail-fast", action="store_true", help="Stop scheduler if one run exits non-zero.")

    pause = subparsers.add_parser("pause", help="Pause operator execution flow.")
    pause.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    pause.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    pause.add_argument("--reason", default="manual_operator_pause", help="Operator pause reason.")

    resume = subparsers.add_parser("resume", help="Resume operator execution flow.")
    resume.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    resume.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )

    review_list = subparsers.add_parser("review-list", help="List trade review candidates.")
    review_list.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_list.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_list.add_argument("--run-id", default=None, help="Optional run id filter.")
    review_list.add_argument(
        "--status",
        choices=("pending_review", "pending", "approved", "rejected", "expired", "all"),
        default="pending_review",
        help="Filter review queue by status.",
    )
    review_list.add_argument("--limit", type=int, default=50, help="Maximum queue items to return. 0 means all.")
    review_list.add_argument("--json", action="store_true", help="Print queue items as JSON.")

    review_show = subparsers.add_parser("review-show", help="Show one trade review candidate by queue id.")
    review_show.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_show.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_show.add_argument("--queue-id", required=True, help="Review queue id.")
    review_show.add_argument("--json", action="store_true", help="Print queue item as JSON.")

    review_approve = subparsers.add_parser("review-approve", help="Approve a trade review candidate.")
    review_approve.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_approve.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_approve.add_argument("--queue-id", required=True, help="Review queue id to approve.")
    review_approve.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_approve.add_argument("--rationale", required=True, help="Operator rationale for approval.")
    review_approve.add_argument("--note", default="", help="Optional additional note.")

    review_reject = subparsers.add_parser("review-reject", help="Reject a trade review candidate.")
    review_reject.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_reject.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_reject.add_argument("--queue-id", required=True, help="Review queue id to reject.")
    review_reject.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_reject.add_argument("--rationale", required=True, help="Operator rationale for rejection.")
    review_reject.add_argument("--note", default="", help="Optional additional note.")

    review_queue = subparsers.add_parser("review-queue", help="Alias of review-list.")
    review_queue.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_queue.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_queue.add_argument("--run-id", default=None, help="Optional run id filter.")
    review_queue.add_argument(
        "--status",
        choices=("pending_review", "pending", "approved", "rejected", "expired", "all"),
        default="pending_review",
        help="Filter review queue by status.",
    )
    review_queue.add_argument("--limit", type=int, default=50, help="Maximum queue items to return. 0 means all.")
    review_queue.add_argument("--json", action="store_true", help="Print queue items as JSON.")

    review_action = subparsers.add_parser("review-action", help="Apply approve/reject/note on a review queue item.")
    review_action.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_action.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_action.add_argument("--queue-id", required=True, help="Review queue id to update.")
    review_action.add_argument(
        "--action",
        choices=("approve", "reject", "note"),
        required=True,
        help="Operator action.",
    )
    review_action.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_action.add_argument("--rationale", required=True, help="Operator rationale for the action.")
    review_action.add_argument("--note", default="", help="Optional additional note.")

    replay = subparsers.add_parser("replay-run", help="Replay a stored run summary from persisted artifacts.")
    replay.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    replay.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    replay.add_argument("--run-id", required=True, help="Run id to replay.")
    replay.add_argument(
        "--include-records",
        action="store_true",
        help="Include reconstructed per-market decisions in stdout JSON output.",
    )
    replay.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    replay_alias = subparsers.add_parser("replay", help="Alias of replay-run with optional --run-id fallback.")
    replay_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    replay_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    replay_alias.add_argument("--run-id", default=None, help="Run id to replay. Defaults to latest known run.")
    replay_alias.add_argument(
        "--include-records",
        action="store_true",
        help="Include reconstructed per-market decisions in stdout JSON output.",
    )
    replay_alias.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    eval_window = subparsers.add_parser("evaluate-window", help="Evaluate a run window from persisted artifacts.")
    eval_window.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    eval_window.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    eval_window.add_argument("--date-from", default=None, help="Inclusive start date (YYYY-MM-DD).")
    eval_window.add_argument("--date-to", default=None, help="Inclusive end date (YYYY-MM-DD).")
    eval_window.add_argument("--limit-runs", type=int, default=50, help="Maximum number of runs to evaluate.")
    eval_window.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    eval_report = subparsers.add_parser(
        "generate-eval-report",
        help="Generate a markdown evaluation report for one run or a date window.",
    )
    eval_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    eval_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    eval_report.add_argument("--run-id", default=None, help="Optional run id to evaluate.")
    eval_report.add_argument("--date-from", default=None, help="Inclusive start date (YYYY-MM-DD).")
    eval_report.add_argument("--date-to", default=None, help="Inclusive end date (YYYY-MM-DD).")
    eval_report.add_argument("--limit-runs", type=int, default=50, help="Maximum runs when evaluating a window.")
    eval_report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    eval_report.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    report = subparsers.add_parser("generate-report", help="Generate a markdown report for a stored run.")
    report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    report.add_argument("--run-id", required=True, help="Run id to report.")
    report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    report.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    smoke_live = subparsers.add_parser("smoke-live-data", help="Fetch one live read-only market data batch.")
    smoke_live.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    smoke_live.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    smoke_live.add_argument("--run-id", default=None, help="Optional explicit run id for this smoke fetch.")
    smoke_live.add_argument(
        "--endpoint",
        default="https://gamma-api.polymarket.com/markets",
        help="Read-only markets API endpoint.",
    )
    smoke_live.add_argument("--timeout-sec", type=float, default=8.0, help="HTTP timeout in seconds.")
    smoke_live.add_argument("--retries", type=int, default=2, help="Number of retry attempts.")
    smoke_live.add_argument(
        "--retry-backoff-sec",
        type=float,
        default=0.5,
        help="Linear backoff base used between retries.",
    )
    smoke_live.add_argument(
        "--max-staleness-sec",
        type=int,
        default=900,
        help="Maximum age in seconds for market snapshots.",
    )
    smoke_live.add_argument("--limit", type=int, default=50, help="Maximum markets requested per batch.")

    smoke_research = subparsers.add_parser(
        "smoke-live-research",
        help="Fetch one structured live research batch and persist raw/normalized findings.",
    )
    smoke_research.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    smoke_research.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    smoke_research.add_argument("--run-id", default=None, help="Optional explicit run id for this smoke fetch.")
    smoke_research.add_argument("--market-id", default="smoke-research-market", help="Synthetic market id.")
    smoke_research.add_argument(
        "--title",
        default="Will inflation decline in the next quarter?",
        help="Synthetic market title used to build the research query.",
    )
    smoke_research.add_argument("--category", default="macro", help="Synthetic market category.")
    smoke_research.add_argument("--query", default=None, help="Optional explicit query override.")
    smoke_research.add_argument("--limit-per-source", type=int, default=5, help="Max records fetched per source.")
    smoke_research.add_argument(
        "--wikipedia-endpoint",
        default="https://en.wikipedia.org/w/api.php",
        help="Wikipedia API endpoint.",
    )
    smoke_research.add_argument(
        "--openalex-endpoint",
        default="https://api.openalex.org/works",
        help="OpenAlex works API endpoint.",
    )
    smoke_research.add_argument("--timeout-sec", type=float, default=8.0, help="HTTP timeout in seconds.")
    smoke_research.add_argument("--retries", type=int, default=2, help="Number of retry attempts.")
    smoke_research.add_argument(
        "--retry-backoff-sec",
        type=float,
        default=0.5,
        help="Linear backoff base used between retries.",
    )
    smoke_research.add_argument("--cache-ttl-sec", type=int, default=600, help="In-memory HTTP cache TTL.")

    portfolio_state = subparsers.add_parser(
        "paper-portfolio-state",
        help="Print current paper portfolio state reconstructed from persisted portfolio events.",
    )
    portfolio_state.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    portfolio_state.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    portfolio_state.add_argument(
        "--run-id",
        default=None,
        help="Optional run id filter. If omitted, all persisted portfolio events are replayed.",
    )

    portfolio_alias = subparsers.add_parser("portfolio", help="Alias of paper-portfolio-state.")
    portfolio_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    portfolio_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    portfolio_alias.add_argument(
        "--run-id",
        default=None,
        help="Optional run id filter. If omitted, all persisted portfolio events are replayed.",
    )

    last_report = subparsers.add_parser("last-report", help="Print the latest markdown or json run report.")
    last_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    last_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    last_report.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Report format to print.",
    )
    last_report.add_argument("--path-only", action="store_true", help="Print only the resolved report path.")

    tx_status = subparsers.add_parser("tx-status", help="Show sandbox transaction status snapshots.")
    tx_status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_status.add_argument("--run-id", default=None, help="Optional run id filter.")
    tx_status.add_argument("--intent-id", default=None, help="Optional intent id filter.")
    tx_status.add_argument("--limit", type=int, default=50, help="Maximum intents to inspect. 0 means all.")
    tx_status.add_argument("--json", action="store_true", help="Print status payload as JSON.")

    tx_reconcile = subparsers.add_parser(
        "tx-reconcile",
        help="Reconcile pending sandbox transactions against chain state.",
    )
    tx_reconcile.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_reconcile.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_reconcile.add_argument("--run-id", default=None, help="Optional run id filter.")
    tx_reconcile.add_argument("--intent-id", default=None, help="Optional intent id filter.")
    tx_reconcile.add_argument("--limit", type=int, default=100, help="Maximum intents to reconcile. 0 means all.")
    tx_reconcile.add_argument("--json", action="store_true", help="Print reconciled payload as JSON.")

    tx_resubmit_safe = subparsers.add_parser(
        "tx-resubmit-safe",
        help="Safely resubmit a sandbox transaction intent when prior attempt is not pending/mined.",
    )
    tx_resubmit_safe.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_resubmit_safe.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_resubmit_safe.add_argument("--intent-id", required=True, help="Intent id to resubmit safely.")
    tx_resubmit_safe.add_argument("--json", action="store_true", help="Print resulting tx snapshot as JSON.")
    return parser
