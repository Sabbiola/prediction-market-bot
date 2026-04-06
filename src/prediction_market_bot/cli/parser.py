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

    beta_dress = subparsers.add_parser(
        "beta-dress-rehearsal",
        help="Run full sandbox-live dress rehearsal and emit explicit GO/NO_GO gate result.",
    )
    beta_dress.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    beta_dress.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    beta_dress.add_argument("--run-id", default=None, help="Optional deterministic run id for rehearsal flow.")
    beta_dress.add_argument("--operator-id", default="beta-operator", help="Operator id used for approval steps.")
    beta_dress.add_argument(
        "--approval-rationale",
        default="Approved during sandbox-live dress rehearsal.",
        help="Rationale used for the review approval step.",
    )
    beta_dress.add_argument(
        "--ui-username",
        default="",
        help="UI username used for authenticated rehearsal checks when ui_auth is enabled.",
    )
    beta_dress.add_argument(
        "--ui-password",
        default="",
        help="UI password used for authenticated rehearsal checks when ui_auth is enabled.",
    )
    beta_dress.add_argument(
        "--report-output",
        type=Path,
        default=None,
        help="Optional markdown report output path. Defaults to artifacts/reports/<run_id>.md.",
    )
    beta_dress.add_argument(
        "--skip-scheduler-probe",
        action="store_true",
        help="Skip the one-iteration scheduler probe step.",
    )
    beta_dress.add_argument("--json", action="store_true", help="Print full rehearsal payload as JSON.")

    live_ready = subparsers.add_parser(
        "live-readiness-check",
        help="Read-only diagnostic: validate all prerequisites for switching to LIVE mode.",
    )
    live_ready.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    live_ready.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    live_ready.add_argument("--json", action="store_true", help="Print result as JSON.")

    scheduler = subparsers.add_parser("run-scheduler", help="Run dry-run scheduler loop from terminal/CI.")
    scheduler.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    scheduler.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    scheduler.add_argument(
        "--interval-sec",
        type=float,
        default=0.0,
        help="Seconds between scheduler ticks. 0 (default) reads scan_interval_sec from app config.",
    )
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

    shadow_report = subparsers.add_parser(
        "generate-shadow-report",
        help="Generate side-by-side shadow comparison report (heuristic vs model-v2 and optional alt-data/LLM path).",
    )
    shadow_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    shadow_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    shadow_report.add_argument("--run-id", required=True, help="Run id with persisted prediction shadow comparisons.")
    shadow_report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    shadow_report.add_argument("--json", action="store_true", help="Print shadow comparison report as JSON.")
    shadow_report.add_argument(
        "--profile",
        action="store_true",
        help="Emit command timing profile summary to stderr/logs.",
    )

    evaluate_promotion = subparsers.add_parser(
        "evaluate-model-promotion",
        help="Evaluate model promotion gate evidence from offline metrics + runtime shadow artifacts.",
    )
    evaluate_promotion.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    evaluate_promotion.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    evaluate_promotion.add_argument("--dataset-id", default=None, help="Optional strategy dataset id override.")
    evaluate_promotion.add_argument("--benchmark-run-id", default=None, help="Optional benchmark run id override.")
    evaluate_promotion.add_argument("--training-run-id", default=None, help="Optional training run id override.")
    evaluate_promotion.add_argument("--walk-forward-run-id", default=None, help="Optional walk-forward run id override.")
    evaluate_promotion.add_argument("--shadow-run-id", default=None, help="Optional shadow scoring run id override.")
    evaluate_promotion.add_argument("--output", type=Path, default=None, help="Optional JSON output file path.")
    evaluate_promotion.add_argument("--json", action="store_true", help="Print evaluation payload as JSON.")

    promotion_status = subparsers.add_parser(
        "model-promotion-status",
        help="Show runtime promotion/rollback gate status and latest drift summary.",
    )
    promotion_status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    promotion_status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    promotion_status.add_argument("--run-id", default=None, help="Optional run id for drift report target.")
    promotion_status.add_argument("--reference-runs", type=int, default=None, help="Optional drift reference run count.")
    promotion_status.add_argument("--json", action="store_true", help="Print status payload as JSON.")

    promote_model = subparsers.add_parser(
        "promote-model-v2",
        help="Persist explicit operator promotion decision for runtime model_v2.",
    )
    promote_model.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    promote_model.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    promote_model.add_argument("--rationale", required=True, help="Operator rationale for promotion.")
    promote_model.add_argument("--model-version", default="", help="Optional explicit model version override.")
    promote_model.add_argument("--dataset-id", default=None, help="Optional dataset id override for evaluation.")
    promote_model.add_argument("--benchmark-run-id", default=None, help="Optional benchmark run id override.")
    promote_model.add_argument("--training-run-id", default=None, help="Optional training run id override.")
    promote_model.add_argument("--walk-forward-run-id", default=None, help="Optional walk-forward run id override.")
    promote_model.add_argument("--shadow-run-id", default=None, help="Optional shadow scoring run id override.")
    promote_model.add_argument(
        "--force",
        action="store_true",
        help="Allow promotion even when gate evaluation fails (requires explicit sign-off).",
    )
    promote_model.add_argument("--json", action="store_true", help="Print promotion payload as JSON.")

    rollback_model = subparsers.add_parser(
        "rollback-model-v2",
        help="Enable rollback flag that forces runtime fallback to heuristic prediction engine.",
    )
    rollback_model.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    rollback_model.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    rollback_model.add_argument("--rationale", required=True, help="Operator rationale for rollback activation.")
    rollback_model.add_argument("--json", action="store_true", help="Print rollback payload as JSON.")

    clear_rollback_model = subparsers.add_parser(
        "clear-model-v2-rollback",
        help="Clear rollback flag to re-enable promoted model_v2 selection checks.",
    )
    clear_rollback_model.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    clear_rollback_model.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    clear_rollback_model.add_argument("--rationale", default="", help="Optional operator rationale for clearing rollback.")
    clear_rollback_model.add_argument("--json", action="store_true", help="Print rollback-clear payload as JSON.")

    drift_status = subparsers.add_parser(
        "drift-status",
        help="Compute model drift report from runtime artifacts and update operator drift status.",
    )
    drift_status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    drift_status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    drift_status.add_argument("--run-id", default=None, help="Optional run id to evaluate for drift.")
    drift_status.add_argument("--reference-runs", type=int, default=None, help="Optional reference run count override.")
    drift_status.add_argument("--json", action="store_true", help="Print drift payload as JSON.")

    threshold_tuning = subparsers.add_parser(
        "record-threshold-tuning",
        help="Record reproducible threshold-tuning decision metadata (audit-only, no runtime auto-tuning).",
    )
    threshold_tuning.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    threshold_tuning.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    threshold_tuning.add_argument("--rationale", required=True, help="Operator rationale for threshold proposal.")
    threshold_tuning.add_argument("--dataset-id", default="", help="Optional dataset id reference.")
    threshold_tuning.add_argument("--target-model-version", default="", help="Optional target model version reference.")
    threshold_tuning.add_argument("--min-confidence", type=float, default=None, help="Proposed min confidence.")
    threshold_tuning.add_argument("--min-edge-bps", type=int, default=None, help="Proposed min edge bps.")
    threshold_tuning.add_argument("--approval-rate-min", type=float, default=None, help="Proposed approval-rate min.")
    threshold_tuning.add_argument("--approval-rate-max", type=float, default=None, help="Proposed approval-rate max.")
    threshold_tuning.add_argument("--ticket", default="", help="Optional change ticket/issue reference.")
    threshold_tuning.add_argument("--json", action="store_true", help="Print tuning record payload as JSON.")

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

    backfill_historical = subparsers.add_parser(
        "backfill-historical-markets",
        help="Backfill resolved-market historical dataset for offline strategy research.",
    )
    backfill_historical.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    backfill_historical.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    backfill_historical.add_argument("--dataset-id", default=None, help="Optional dataset id override.")
    backfill_historical.add_argument("--page-size", type=int, default=None, help="Optional page size override.")
    backfill_historical.add_argument("--max-pages", type=int, default=None, help="Optional max pages per run override.")
    backfill_historical.add_argument("--start-cursor", default=None, help="Optional cursor override for resumable backfill.")
    backfill_historical.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    backfill_historical.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset checkpoint before running backfill.",
    )
    backfill_historical.add_argument("--date-from", default=None, help="Inclusive date filter start (YYYY-MM-DD).")
    backfill_historical.add_argument("--date-to", default=None, help="Inclusive date filter end (YYYY-MM-DD).")
    backfill_historical.add_argument("--json", action="store_true", help="Print backfill summary payload as JSON.")

    inspect_dataset = subparsers.add_parser(
        "inspect-dataset",
        help="Inspect historical strategy-research dataset layout and counts.",
    )
    inspect_dataset.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_dataset.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_dataset.add_argument("--dataset-id", default=None, help="Optional dataset id override.")
    inspect_dataset.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    inspect_dataset.add_argument("--json", action="store_true", help="Print dataset inspection payload as JSON.")

    verify_dataset = subparsers.add_parser(
        "verify-dataset",
        help="Verify historical strategy-research dataset consistency and completeness.",
    )
    verify_dataset.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_dataset.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_dataset.add_argument("--dataset-id", default=None, help="Optional dataset id override.")
    verify_dataset.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    verify_dataset.add_argument("--json", action="store_true", help="Print dataset verification payload as JSON.")

    backfill_research = subparsers.add_parser(
        "backfill-research-evidence",
        help="Backfill offline research evidence corpus aligned to market decision timelines.",
    )
    backfill_research.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    backfill_research.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    backfill_research.add_argument("--corpus-id", default=None, help="Optional research corpus id override.")
    backfill_research.add_argument(
        "--source-dataset-id",
        default=None,
        help="Historical market dataset id used for market timeline alignment.",
    )
    backfill_research.add_argument("--limit-markets", type=int, default=None, help="Optional max markets per run.")
    backfill_research.add_argument(
        "--limit-per-source",
        type=int,
        default=None,
        help="Optional max findings requested per source.",
    )
    backfill_research.add_argument(
        "--decision-date-from",
        default=None,
        help="Inclusive decision date filter start (YYYY-MM-DD).",
    )
    backfill_research.add_argument(
        "--decision-date-to",
        default=None,
        help="Inclusive decision date filter end (YYYY-MM-DD).",
    )
    backfill_research.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    backfill_research.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset research corpus checkpoint before backfill.",
    )
    backfill_research.add_argument("--json", action="store_true", help="Print backfill summary payload as JSON.")

    inspect_research = subparsers.add_parser(
        "inspect-research-corpus",
        help="Inspect offline research evidence corpus layout and counts.",
    )
    inspect_research.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_research.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_research.add_argument("--corpus-id", default=None, help="Optional research corpus id override.")
    inspect_research.add_argument("--source-dataset-id", default=None, help="Optional source dataset id override.")
    inspect_research.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    inspect_research.add_argument("--json", action="store_true", help="Print corpus inspection payload as JSON.")

    verify_research = subparsers.add_parser(
        "verify-research-alignment",
        help="Verify offline research evidence timeline alignment and provenance integrity.",
    )
    verify_research.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_research.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_research.add_argument("--corpus-id", default=None, help="Optional research corpus id override.")
    verify_research.add_argument("--source-dataset-id", default=None, help="Optional source dataset id override.")
    verify_research.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    verify_research.add_argument("--json", action="store_true", help="Print verification payload as JSON.")

    backfill_news = subparsers.add_parser(
        "backfill-news",
        help="Backfill Google News/RSS-style offline news corpus for strategy research.",
    )
    backfill_news.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    backfill_news.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    backfill_news.add_argument("--corpus-id", default=None, help="Optional news corpus id override.")
    backfill_news.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Topic/category query (repeatable), example: WORLD or BUSINESS.",
    )
    backfill_news.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword/entity query (repeatable).",
    )
    backfill_news.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum articles fetched per query.",
    )
    backfill_news.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    backfill_news.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset news corpus checkpoint before backfill.",
    )
    backfill_news.add_argument("--json", action="store_true", help="Print backfill summary payload as JSON.")

    inspect_news = subparsers.add_parser(
        "inspect-news-corpus",
        help="Inspect offline news corpus layout and counts.",
    )
    inspect_news.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_news.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_news.add_argument("--corpus-id", default=None, help="Optional news corpus id override.")
    inspect_news.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    inspect_news.add_argument("--json", action="store_true", help="Print corpus inspection payload as JSON.")

    verify_news_source = subparsers.add_parser(
        "verify-news-source",
        help="Verify news source access and corpus consistency.",
    )
    verify_news_source.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_news_source.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_news_source.add_argument("--corpus-id", default=None, help="Optional news corpus id override.")
    verify_news_source.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    verify_news_source.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Topic/category query to probe source readiness (repeatable).",
    )
    verify_news_source.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword/entity query to probe source readiness (repeatable).",
    )
    verify_news_source.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum articles fetched per probe query.",
    )
    verify_news_source.add_argument("--json", action="store_true", help="Print verification payload as JSON.")

    backfill_reddit = subparsers.add_parser(
        "backfill-reddit",
        help="Backfill offline Reddit corpus with OAuth-backed submissions/comments.",
    )
    backfill_reddit.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    backfill_reddit.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    backfill_reddit.add_argument("--corpus-id", default=None, help="Optional reddit corpus id override.")
    backfill_reddit.add_argument(
        "--subreddit",
        action="append",
        default=[],
        help="Subreddit query (repeatable), example: worldnews.",
    )
    backfill_reddit.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword query (repeatable).",
    )
    backfill_reddit.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum submissions fetched per query page.",
    )
    backfill_reddit.add_argument(
        "--max-pages-per-query",
        type=int,
        default=None,
        help="Optional maximum pages fetched per query. 0 means unbounded.",
    )
    include_comments_group = backfill_reddit.add_mutually_exclusive_group()
    include_comments_group.add_argument(
        "--include-comments",
        dest="include_comments",
        action="store_true",
        help="Include comments ingest per submission.",
    )
    include_comments_group.add_argument(
        "--no-include-comments",
        dest="include_comments",
        action="store_false",
        help="Disable comments ingest per submission.",
    )
    backfill_reddit.set_defaults(include_comments=None)
    backfill_reddit.add_argument(
        "--comment-limit-per-post",
        type=int,
        default=None,
        help="Optional maximum comments fetched per submission when comments are enabled.",
    )
    backfill_reddit.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    backfill_reddit.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset reddit corpus checkpoint before backfill.",
    )
    backfill_reddit.add_argument(
        "--incremental",
        action="store_true",
        help="Run incremental mode (probe recent page per query with dedup).",
    )
    backfill_reddit.add_argument("--json", action="store_true", help="Print backfill summary payload as JSON.")

    inspect_reddit = subparsers.add_parser(
        "inspect-reddit-corpus",
        help="Inspect offline Reddit corpus layout and counts.",
    )
    inspect_reddit.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_reddit.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_reddit.add_argument("--corpus-id", default=None, help="Optional reddit corpus id override.")
    inspect_reddit.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    inspect_reddit.add_argument("--json", action="store_true", help="Print corpus inspection payload as JSON.")

    verify_reddit = subparsers.add_parser(
        "verify-reddit-oauth",
        help="Verify Reddit OAuth source access and corpus consistency.",
    )
    verify_reddit.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_reddit.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_reddit.add_argument("--corpus-id", default=None, help="Optional reddit corpus id override.")
    verify_reddit.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    verify_reddit.add_argument(
        "--subreddit",
        action="append",
        default=[],
        help="Subreddit query to probe source readiness (repeatable).",
    )
    verify_reddit.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword query to probe source readiness (repeatable).",
    )
    verify_reddit.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum submissions fetched per probe query.",
    )
    verify_comments_group = verify_reddit.add_mutually_exclusive_group()
    verify_comments_group.add_argument(
        "--include-comments",
        dest="include_comments",
        action="store_true",
        help="Probe comments ingest per submission.",
    )
    verify_comments_group.add_argument(
        "--no-include-comments",
        dest="include_comments",
        action="store_false",
        help="Skip comment probe calls.",
    )
    verify_reddit.set_defaults(include_comments=None)
    verify_reddit.add_argument(
        "--comment-limit-per-post",
        type=int,
        default=None,
        help="Optional maximum comments fetched per submission during source verification.",
    )
    verify_reddit.add_argument("--json", action="store_true", help="Print verification payload as JSON.")

    backfill_x = subparsers.add_parser(
        "backfill-x",
        help="Backfill offline X corpus with capability-gated API ingestion.",
    )
    backfill_x.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    backfill_x.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    backfill_x.add_argument("--corpus-id", default=None, help="Optional X corpus id override.")
    backfill_x.add_argument(
        "--account",
        action="append",
        default=[],
        help="Tracked account query (repeatable), example: elonmusk.",
    )
    backfill_x.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword query (repeatable).",
    )
    backfill_x.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum posts fetched per query page.",
    )
    backfill_x.add_argument(
        "--max-pages-per-query",
        type=int,
        default=None,
        help="Optional maximum pages fetched per query. 0 means unbounded.",
    )
    backfill_x.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    backfill_x.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset X corpus checkpoint before backfill.",
    )
    backfill_x.add_argument(
        "--incremental",
        action="store_true",
        help="Run incremental mode (probe recent page per query with dedup).",
    )
    backfill_x.add_argument("--json", action="store_true", help="Print backfill summary payload as JSON.")

    inspect_x = subparsers.add_parser(
        "inspect-x-corpus",
        help="Inspect offline X corpus layout and counts.",
    )
    inspect_x.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_x.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_x.add_argument("--corpus-id", default=None, help="Optional X corpus id override.")
    inspect_x.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    inspect_x.add_argument("--json", action="store_true", help="Print corpus inspection payload as JSON.")

    verify_x = subparsers.add_parser(
        "verify-x-source",
        help="Verify X source access and corpus consistency.",
    )
    verify_x.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_x.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_x.add_argument("--corpus-id", default=None, help="Optional X corpus id override.")
    verify_x.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    verify_x.add_argument(
        "--account",
        action="append",
        default=[],
        help="Tracked account query to probe source readiness (repeatable).",
    )
    verify_x.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Keyword query to probe source readiness (repeatable).",
    )
    verify_x.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help="Optional maximum posts fetched per probe query.",
    )
    verify_x.add_argument("--json", action="store_true", help="Print verification payload as JSON.")

    build_linkage = subparsers.add_parser(
        "build-linkage",
        help="Build offline deterministic evidence->event/market linkage artifacts.",
    )
    build_linkage.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    build_linkage.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    build_linkage.add_argument("--linkage-id", default=None, help="Optional linkage artifact id override.")
    build_linkage.add_argument("--dataset-id", default=None, help="Optional historical dataset id override.")
    build_linkage.add_argument("--news-corpus-id", default=None, help="Optional news corpus id override.")
    build_linkage.add_argument("--reddit-corpus-id", default=None, help="Optional reddit corpus id override.")
    build_linkage.add_argument("--x-corpus-id", default=None, help="Optional X corpus id override.")
    build_linkage.add_argument("--checkpoint-path", type=Path, default=None, help="Optional checkpoint path override.")
    build_linkage.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset linkage checkpoint before processing.",
    )
    build_linkage.add_argument(
        "--limit-evidence",
        type=int,
        default=None,
        help="Optional maximum number of evidence rows processed in this run.",
    )
    build_linkage.add_argument(
        "--decision-date-from",
        default=None,
        help="Optional lower date bound for market decision timestamp (YYYY-MM-DD).",
    )
    build_linkage.add_argument(
        "--decision-date-to",
        default=None,
        help="Optional upper date bound for market decision timestamp (YYYY-MM-DD).",
    )
    build_linkage.add_argument("--json", action="store_true", help="Print linkage build payload as JSON.")

    inspect_linkage = subparsers.add_parser(
        "inspect-linkage",
        help="Inspect linkage artifact counts/checkpoint and state distribution.",
    )
    inspect_linkage.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_linkage.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_linkage.add_argument("--linkage-id", default=None, help="Optional linkage artifact id override.")
    inspect_linkage.add_argument("--dataset-id", default=None, help="Optional historical dataset id override.")
    inspect_linkage.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    inspect_linkage.add_argument("--json", action="store_true", help="Print linkage inspection payload as JSON.")

    verify_linkage = subparsers.add_parser(
        "verify-linkage-quality",
        help="Verify linkage quality constraints and explicit unresolved/ambiguous states.",
    )
    verify_linkage.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_linkage.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_linkage.add_argument("--linkage-id", default=None, help="Optional linkage artifact id override.")
    verify_linkage.add_argument("--dataset-id", default=None, help="Optional historical dataset id override.")
    verify_linkage.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    verify_linkage.add_argument("--json", action="store_true", help="Print linkage verification payload as JSON.")

    enrich_alt_data = subparsers.add_parser(
        "enrich-alt-data",
        help="Run offline LLM enrichment over linked alt-data evidence records.",
    )
    enrich_alt_data.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    enrich_alt_data.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    enrich_alt_data.add_argument("--enrichment-id", default=None, help="Optional enrichment artifact id override.")
    enrich_alt_data.add_argument("--linkage-id", default=None, help="Optional linkage artifact id override.")
    enrich_alt_data.add_argument("--news-corpus-id", default=None, help="Optional news corpus id override.")
    enrich_alt_data.add_argument("--reddit-corpus-id", default=None, help="Optional reddit corpus id override.")
    enrich_alt_data.add_argument("--x-corpus-id", default=None, help="Optional x corpus id override.")
    enrich_alt_data.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    enrich_alt_data.add_argument("--reset-checkpoint", action="store_true", help="Reset enrichment checkpoint first.")
    enrich_alt_data.add_argument(
        "--limit-records",
        type=int,
        default=None,
        help="Optional maximum linked evidence rows processed in this run.",
    )
    enrich_alt_data.add_argument("--json", action="store_true", help="Print enrichment summary payload as JSON.")

    inspect_llm_enrichment = subparsers.add_parser(
        "inspect-llm-enrichment",
        help="Inspect LLM enrichment artifact counts/checkpoint and status distribution.",
    )
    inspect_llm_enrichment.add_argument(
        "--config",
        default="config/app.yaml",
        type=Path,
        help="Path to app config YAML.",
    )
    inspect_llm_enrichment.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_llm_enrichment.add_argument("--enrichment-id", default=None, help="Optional enrichment artifact id override.")
    inspect_llm_enrichment.add_argument("--linkage-id", default=None, help="Optional linkage artifact id override.")
    inspect_llm_enrichment.add_argument(
        "--checkpoint-path",
        type=Path,
        default=None,
        help="Optional checkpoint path override.",
    )
    inspect_llm_enrichment.add_argument("--json", action="store_true", help="Print enrichment inspection payload as JSON.")

    build_labels = subparsers.add_parser(
        "build-labels",
        help="Build reproducible leakage-safe labels from resolved historical markets.",
    )
    build_labels.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    build_labels.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    build_labels.add_argument("--dataset-id", default=None, help="Historical dataset id to label.")
    build_labels.add_argument("--corpus-id", default=None, help="Optional research corpus id for feature enrichment.")
    build_labels.add_argument("--labels-path", type=Path, default=None, help="Optional labels output path override.")
    build_labels.add_argument("--json", action="store_true", help="Print labels build payload as JSON.")

    run_benchmarks = subparsers.add_parser(
        "run-benchmarks",
        help="Run baseline benchmarks on labeled historical dataset splits.",
    )
    run_benchmarks.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_benchmarks.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_benchmarks.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    run_benchmarks.add_argument("--labels-path", type=Path, default=None, help="Optional labels path override.")
    run_benchmarks.add_argument(
        "--split-mode",
        choices=("holdout", "walk-forward"),
        default="holdout",
        help="Temporal split mode.",
    )
    run_benchmarks.add_argument("--train-ratio", type=float, default=0.6, help="Holdout train ratio.")
    run_benchmarks.add_argument("--validation-ratio", type=float, default=0.2, help="Holdout validation ratio.")
    run_benchmarks.add_argument("--train-days", type=int, default=120, help="Walk-forward train window days.")
    run_benchmarks.add_argument(
        "--validation-days",
        type=int,
        default=30,
        help="Walk-forward validation window days.",
    )
    run_benchmarks.add_argument("--test-days", type=int, default=30, help="Walk-forward test window days.")
    run_benchmarks.add_argument("--step-days", type=int, default=30, help="Walk-forward step days.")
    run_benchmarks.add_argument("--max-folds", type=int, default=0, help="Max walk-forward folds. 0 means all.")
    run_benchmarks.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Approval threshold confidence override.",
    )
    run_benchmarks.add_argument(
        "--min-edge-bps",
        type=int,
        default=None,
        help="Approval threshold edge bps override.",
    )
    run_benchmarks.add_argument("--json", action="store_true", help="Print benchmark summary payload as JSON.")

    compare_benchmarks = subparsers.add_parser(
        "compare-benchmarks",
        help="Compare two benchmark runs (or latest two if omitted).",
    )
    compare_benchmarks.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    compare_benchmarks.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    compare_benchmarks.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    compare_benchmarks.add_argument("--run-a", default=None, help="First benchmark run id.")
    compare_benchmarks.add_argument("--run-b", default=None, help="Second benchmark run id.")
    compare_benchmarks.add_argument(
        "--split",
        choices=("train", "validation", "test"),
        default="test",
        help="Split to compare.",
    )
    compare_benchmarks.add_argument("--json", action="store_true", help="Print comparison payload as JSON.")

    run_ablation = subparsers.add_parser(
        "run-ablation-study",
        help="Run offline ablation study for market/research/news/reddit/x/LLM feature variants.",
    )
    run_ablation.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_ablation.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_ablation.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    run_ablation.add_argument("--labels-path", type=Path, default=None, help="Optional labels path override.")
    run_ablation.add_argument(
        "--alt-feature-rows-path",
        type=Path,
        default=None,
        help="Optional alt_feature_rows.jsonl path override.",
    )
    run_ablation.add_argument(
        "--split-mode",
        choices=("holdout", "walk-forward"),
        default="walk-forward",
        help="Temporal split mode.",
    )
    run_ablation.add_argument("--train-ratio", type=float, default=0.6, help="Holdout train ratio.")
    run_ablation.add_argument("--validation-ratio", type=float, default=0.2, help="Holdout validation ratio.")
    run_ablation.add_argument("--train-days", type=int, default=120, help="Walk-forward train window days.")
    run_ablation.add_argument("--validation-days", type=int, default=30, help="Walk-forward validation window days.")
    run_ablation.add_argument("--test-days", type=int, default=30, help="Walk-forward test window days.")
    run_ablation.add_argument("--step-days", type=int, default=30, help="Walk-forward step days.")
    run_ablation.add_argument("--max-folds", type=int, default=0, help="Max walk-forward folds. 0 means all.")
    run_ablation.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Approval threshold confidence override.",
    )
    run_ablation.add_argument(
        "--min-edge-bps",
        type=int,
        default=None,
        help="Approval threshold edge bps override.",
    )
    run_ablation.add_argument("--json", action="store_true", help="Print ablation summary payload as JSON.")

    compare_alt_variants = subparsers.add_parser(
        "compare-alt-data-variants",
        help="Compare variant outcomes from an ablation run against a reference variant.",
    )
    compare_alt_variants.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    compare_alt_variants.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    compare_alt_variants.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    compare_alt_variants.add_argument("--run-id", default=None, help="Ablation run id. Defaults to latest.")
    compare_alt_variants.add_argument(
        "--split",
        choices=("train", "validation", "test"),
        default="test",
        help="Split to compare.",
    )
    compare_alt_variants.add_argument(
        "--reference-variant",
        choices=(
            "market_only_baseline",
            "market_plus_research_baseline",
            "market_plus_news",
            "market_plus_reddit",
            "market_plus_x",
            "market_plus_alt_data_without_llm",
            "market_plus_alt_data_with_llm_enrichment",
        ),
        default="market_only_baseline",
        help="Reference variant used for deltas.",
    )
    compare_alt_variants.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    compare_alt_variants.add_argument("--json", action="store_true", help="Print comparison payload as JSON.")

    run_walk_forward = subparsers.add_parser(
        "run-walk-forward",
        help="Run leakage-safe walk-forward strategy simulation with bankroll/slippage assumptions.",
    )
    run_walk_forward.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_walk_forward.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_walk_forward.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    run_walk_forward.add_argument("--labels-path", type=Path, default=None, help="Optional labels path override.")
    run_walk_forward.add_argument(
        "--baseline-name",
        choices=(
            "market_implied",
            "fifty_fifty",
            "category_prior",
            "heuristic_prediction_agent",
            "research_only",
            "momentum_structure",
        ),
        default="heuristic_prediction_agent",
        help="Baseline predictor used for strategy simulation.",
    )
    run_walk_forward.add_argument(
        "--eval-split",
        choices=("validation", "test"),
        default="test",
        help="Fold split used for strategy simulation.",
    )
    run_walk_forward.add_argument("--train-days", type=int, default=120, help="Walk-forward train window days.")
    run_walk_forward.add_argument(
        "--validation-days",
        type=int,
        default=30,
        help="Walk-forward validation window days.",
    )
    run_walk_forward.add_argument("--test-days", type=int, default=30, help="Walk-forward test window days.")
    run_walk_forward.add_argument("--step-days", type=int, default=30, help="Walk-forward step days.")
    run_walk_forward.add_argument("--max-folds", type=int, default=0, help="Max walk-forward folds. 0 means all.")
    run_walk_forward.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Approval threshold confidence override.",
    )
    run_walk_forward.add_argument(
        "--min-edge-bps",
        type=int,
        default=None,
        help="Approval threshold edge bps override.",
    )
    run_walk_forward.add_argument(
        "--initial-bankroll-usd",
        type=float,
        default=10_000.0,
        help="Initial bankroll used for each fold simulation.",
    )
    run_walk_forward.add_argument(
        "--base-position-pct",
        type=float,
        default=0.02,
        help="Base position sizing percentage.",
    )
    run_walk_forward.add_argument(
        "--max-position-pct",
        type=float,
        default=0.05,
        help="Maximum position sizing percentage cap.",
    )
    run_walk_forward.add_argument(
        "--min-stake-usd",
        type=float,
        default=25.0,
        help="Minimum stake required for approved trades.",
    )
    run_walk_forward.add_argument("--fee-bps", type=int, default=20, help="Fee assumption in basis points.")
    run_walk_forward.add_argument(
        "--slippage-bps",
        type=int,
        default=10,
        help="Slippage assumption in basis points.",
    )
    run_walk_forward.add_argument("--json", action="store_true", help="Print walk-forward payload as JSON.")

    strategy_report = subparsers.add_parser(
        "generate-strategy-report",
        help="Generate markdown report for a walk-forward strategy run.",
    )
    strategy_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    strategy_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    strategy_report.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    strategy_report.add_argument("--run-id", default=None, help="Walk-forward run id. Defaults to latest.")
    strategy_report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    strategy_report.add_argument("--json", action="store_true", help="Print report payload as JSON.")

    build_feature_dataset = subparsers.add_parser(
        "build-feature-dataset",
        help="Build leakage-safe offline feature dataset at decision timestamps.",
    )
    build_feature_dataset.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    build_feature_dataset.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    build_feature_dataset.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    build_feature_dataset.add_argument(
        "--corpus-id",
        default=None,
        help="Optional research corpus id used for research aggregate features.",
    )
    build_feature_dataset.add_argument("--json", action="store_true", help="Print feature build payload as JSON.")

    inspect_feature_schema = subparsers.add_parser(
        "inspect-feature-schema",
        help="Inspect feature schema contract and current dataset artifact paths/counts.",
    )
    inspect_feature_schema.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_feature_schema.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_feature_schema.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    inspect_feature_schema.add_argument("--json", action="store_true", help="Print feature schema payload as JSON.")

    verify_feature_parity = subparsers.add_parser(
        "verify-feature-parity",
        help="Verify feature rows/schema version parity for offline model contract safety.",
    )
    verify_feature_parity.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_feature_parity.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_feature_parity.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    verify_feature_parity.add_argument("--json", action="store_true", help="Print parity verification payload as JSON.")

    build_alt_feature_dataset = subparsers.add_parser(
        "build-alt-feature-dataset",
        help="Build versioned offline alt-data feature dataset from linkage + news/reddit/x + enrichment artifacts.",
    )
    build_alt_feature_dataset.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    build_alt_feature_dataset.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    build_alt_feature_dataset.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    build_alt_feature_dataset.add_argument("--linkage-id", default=None, help="Optional linkage id override.")
    build_alt_feature_dataset.add_argument("--news-corpus-id", default=None, help="Optional news corpus id override.")
    build_alt_feature_dataset.add_argument("--reddit-corpus-id", default=None, help="Optional reddit corpus id override.")
    build_alt_feature_dataset.add_argument("--x-corpus-id", default=None, help="Optional X corpus id override.")
    build_alt_feature_dataset.add_argument("--enrichment-id", default=None, help="Optional enrichment id override.")
    build_alt_feature_dataset.add_argument("--json", action="store_true", help="Print alt feature build payload as JSON.")

    inspect_alt_feature_schema = subparsers.add_parser(
        "inspect-alt-feature-schema",
        help="Inspect alt-data feature schema contract and dataset artifact paths/counts.",
    )
    inspect_alt_feature_schema.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    inspect_alt_feature_schema.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    inspect_alt_feature_schema.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    inspect_alt_feature_schema.add_argument("--json", action="store_true", help="Print alt feature schema payload as JSON.")

    verify_alt_feature_parity = subparsers.add_parser(
        "verify-alt-feature-parity",
        help="Verify alt-data feature rows/schema version parity for offline model contract safety.",
    )
    verify_alt_feature_parity.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    verify_alt_feature_parity.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    verify_alt_feature_parity.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    verify_alt_feature_parity.add_argument("--json", action="store_true", help="Print alt feature parity payload as JSON.")

    train_models = subparsers.add_parser(
        "train-baseline-models",
        help="Train offline baseline models on leakage-safe feature dataset.",
    )
    train_models.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    train_models.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    train_models.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    train_models.add_argument(
        "--feature-rows-path",
        type=Path,
        default=None,
        help="Optional feature_rows.jsonl path override.",
    )
    train_models.add_argument(
        "--split-mode",
        choices=("holdout",),
        default="holdout",
        help="Temporal split mode for training.",
    )
    train_models.add_argument("--train-ratio", type=float, default=0.6, help="Holdout train ratio.")
    train_models.add_argument("--validation-ratio", type=float, default=0.2, help="Holdout validation ratio.")
    train_models.add_argument("--window-days", type=int, default=30, help="Window size used for window metrics.")
    train_models.add_argument(
        "--no-xgboost",
        dest="include_xgboost",
        action="store_false",
        help="Disable xgboost candidate training for this run.",
    )
    train_models.set_defaults(include_xgboost=True)
    train_models.add_argument("--json", action="store_true", help="Print training summary payload as JSON.")

    calibrate_model = subparsers.add_parser(
        "calibrate-model",
        help="Fit probability calibration (platt/isotonic) on an offline training run.",
    )
    calibrate_model.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    calibrate_model.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    calibrate_model.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    calibrate_model.add_argument("--run-id", default=None, help="Training run id. Defaults to latest.")
    calibrate_model.add_argument("--model-name", default=None, help="Model name. Defaults to best model in run.")
    calibrate_model.add_argument(
        "--method",
        choices=("platt", "isotonic"),
        default="platt",
        help="Calibration method.",
    )
    calibrate_model.add_argument(
        "--fit-split",
        choices=("train", "validation", "test"),
        default="validation",
        help="Split used to fit the calibrator.",
    )
    calibrate_model.add_argument(
        "--eval-split",
        choices=("train", "validation", "test"),
        default="test",
        help="Split used to evaluate raw vs calibrated probabilities.",
    )
    calibrate_model.add_argument("--json", action="store_true", help="Print calibration payload as JSON.")

    compare_models = subparsers.add_parser(
        "compare-models",
        help="Compare trained offline models on a selected split.",
    )
    compare_models.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    compare_models.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    compare_models.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    compare_models.add_argument("--run-id", default=None, help="Training run id. Defaults to latest.")
    compare_models.add_argument(
        "--split",
        choices=("train", "validation", "test"),
        default="test",
        help="Split used to rank model performance.",
    )
    compare_models.add_argument("--json", action="store_true", help="Print comparison payload as JSON.")

    generate_model_card = subparsers.add_parser(
        "generate-model-card",
        help="Generate a model card markdown artifact for an offline training run.",
    )
    generate_model_card.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    generate_model_card.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    generate_model_card.add_argument("--dataset-id", default=None, help="Historical dataset id.")
    generate_model_card.add_argument("--run-id", default=None, help="Training run id. Defaults to latest.")
    generate_model_card.add_argument("--model-name", default=None, help="Model name. Defaults to best model in run.")
    generate_model_card.add_argument(
        "--calibration-run-id",
        default=None,
        help="Optional calibration run id to include in card.",
    )
    generate_model_card.add_argument("--owner", default="strategy-research", help="Model owner in card metadata.")
    generate_model_card.add_argument(
        "--decision",
        choices=("approved", "rejected", "needs_more_evidence"),
        default="needs_more_evidence",
        help="Model promotion decision status to render in card.",
    )
    generate_model_card.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")
    generate_model_card.add_argument("--json", action="store_true", help="Print model-card payload as JSON.")

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
