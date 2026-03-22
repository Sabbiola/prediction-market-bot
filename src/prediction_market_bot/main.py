from __future__ import annotations

from prediction_market_bot.app.bootstrap import build_persistence
from prediction_market_bot.cli.app import main
from prediction_market_bot.cli.commands.db_commands import (
    db_backup_command,
    db_current_version_command,
    db_init_command,
    db_restore_command,
    db_upgrade_command,
    db_verify_command,
)
from prediction_market_bot.cli.commands.health_commands import healthcheck_command, validate_startup_command
from prediction_market_bot.cli.commands.model_promotion_commands import (
    clear_model_v2_rollback_command,
    drift_status_command,
    evaluate_model_promotion_command,
    model_promotion_status_command,
    promote_model_v2_command,
    record_threshold_tuning_command,
    rollback_model_v2_command,
)
from prediction_market_bot.cli.commands.ops_commands import pause_command, resume_command, status_command
from prediction_market_bot.cli.commands.report_commands import (
    generate_report_command,
    generate_shadow_report_command,
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
from prediction_market_bot.cli.commands.strategy_research_commands import (
    backfill_historical_markets_command,
    backfill_research_evidence_command,
    build_alt_feature_dataset_command,
    build_feature_dataset_command,
    build_labels_command,
    compare_alt_data_variants_command,
    compare_benchmarks_command,
    enrich_alt_data_command,
    generate_strategy_report_command,
    inspect_alt_feature_schema_command,
    inspect_dataset_command,
    inspect_feature_schema_command,
    inspect_llm_enrichment_command,
    inspect_research_corpus_command,
    run_ablation_study_command,
    run_walk_forward_command,
    run_benchmarks_command,
    verify_alt_feature_parity_command,
    verify_feature_parity_command,
    verify_research_alignment_command,
    verify_dataset_command,
)
from prediction_market_bot.cli.commands.tx_commands import (
    tx_reconcile_command,
    tx_resubmit_safe_command,
    tx_status_command,
)
from prediction_market_bot.cli.parser import build_parser

__all__ = [
    "build_parser",
    "build_persistence",
    "db_current_version_command",
    "db_init_command",
    "db_backup_command",
    "db_restore_command",
    "db_upgrade_command",
    "db_verify_command",
    "evaluate_window_command",
    "generate_eval_report_command",
    "generate_report_command",
    "generate_shadow_report_command",
    "evaluate_model_promotion_command",
    "model_promotion_status_command",
    "promote_model_v2_command",
    "rollback_model_v2_command",
    "clear_model_v2_rollback_command",
    "drift_status_command",
    "record_threshold_tuning_command",
    "healthcheck_command",
    "last_report_command",
    "main",
    "paper_portfolio_state_command",
    "pause_command",
    "replay_run_command",
    "resume_command",
    "review_action_command",
    "review_approve_command",
    "review_list_command",
    "review_reject_command",
    "review_show_command",
    "run_once_command",
    "run_scheduler_command",
    "settle_run_command",
    "smoke_live_data_command",
    "smoke_live_research_command",
    "backfill_historical_markets_command",
    "backfill_research_evidence_command",
    "build_alt_feature_dataset_command",
    "build_labels_command",
    "compare_alt_data_variants_command",
    "enrich_alt_data_command",
    "build_feature_dataset_command",
    "run_benchmarks_command",
    "run_ablation_study_command",
    "compare_benchmarks_command",
    "run_walk_forward_command",
    "generate_strategy_report_command",
    "inspect_dataset_command",
    "inspect_alt_feature_schema_command",
    "inspect_feature_schema_command",
    "inspect_llm_enrichment_command",
    "inspect_research_corpus_command",
    "verify_alt_feature_parity_command",
    "verify_feature_parity_command",
    "verify_research_alignment_command",
    "verify_dataset_command",
    "status_command",
    "tx_reconcile_command",
    "tx_resubmit_safe_command",
    "tx_status_command",
    "validate_startup_command",
]


if __name__ == "__main__":
    raise SystemExit(main())
