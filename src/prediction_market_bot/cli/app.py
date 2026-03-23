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
from prediction_market_bot.cli.commands.rehearsal_commands import beta_dress_rehearsal_command
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
    backfill_news_command,
    backfill_reddit_command,
    backfill_x_command,
    build_linkage_command,
    backfill_historical_markets_command,
    build_alt_feature_dataset_command,
    backfill_research_evidence_command,
    build_feature_dataset_command,
    build_labels_command,
    calibrate_model_command,
    compare_alt_data_variants_command,
    compare_benchmarks_command,
    compare_models_command,
    enrich_alt_data_command,
    generate_model_card_command,
    generate_strategy_report_command,
    inspect_dataset_command,
    inspect_feature_schema_command,
    inspect_alt_feature_schema_command,
    inspect_linkage_command,
    inspect_llm_enrichment_command,
    inspect_news_corpus_command,
    inspect_reddit_corpus_command,
    inspect_x_corpus_command,
    inspect_research_corpus_command,
    run_ablation_study_command,
    train_baseline_models_command,
    verify_news_source_command,
    verify_reddit_oauth_command,
    verify_x_source_command,
    run_walk_forward_command,
    run_benchmarks_command,
    verify_feature_parity_command,
    verify_alt_feature_parity_command,
    verify_linkage_quality_command,
    verify_research_alignment_command,
    verify_dataset_command,
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
    if args.command == "beta-dress-rehearsal":
        return beta_dress_rehearsal_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            operator_id=args.operator_id,
            approval_rationale=args.approval_rationale,
            ui_username=args.ui_username,
            ui_password=args.ui_password,
            report_output_path=args.report_output,
            skip_scheduler_probe=args.skip_scheduler_probe,
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
    if args.command == "generate-shadow-report":
        return generate_shadow_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            output_path=args.output,
            as_json=args.json,
            profile=args.profile,
        )
    if args.command == "evaluate-model-promotion":
        return evaluate_model_promotion_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            benchmark_run_id=args.benchmark_run_id,
            training_run_id=args.training_run_id,
            walk_forward_run_id=args.walk_forward_run_id,
            shadow_run_id=args.shadow_run_id,
            output_path=args.output,
            as_json=args.json,
        )
    if args.command == "model-promotion-status":
        return model_promotion_status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            reference_runs=args.reference_runs,
            as_json=args.json,
        )
    if args.command == "promote-model-v2":
        return promote_model_v2_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            rationale=args.rationale,
            model_version=args.model_version,
            dataset_id=args.dataset_id,
            benchmark_run_id=args.benchmark_run_id,
            training_run_id=args.training_run_id,
            walk_forward_run_id=args.walk_forward_run_id,
            shadow_run_id=args.shadow_run_id,
            force=args.force,
            as_json=args.json,
        )
    if args.command == "rollback-model-v2":
        return rollback_model_v2_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            rationale=args.rationale,
            as_json=args.json,
        )
    if args.command == "clear-model-v2-rollback":
        return clear_model_v2_rollback_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            rationale=args.rationale,
            as_json=args.json,
        )
    if args.command == "drift-status":
        return drift_status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            reference_runs=args.reference_runs,
            as_json=args.json,
        )
    if args.command == "record-threshold-tuning":
        return record_threshold_tuning_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            rationale=args.rationale,
            dataset_id=args.dataset_id,
            target_model_version=args.target_model_version,
            min_confidence=args.min_confidence,
            min_edge_bps=args.min_edge_bps,
            approval_rate_min=args.approval_rate_min,
            approval_rate_max=args.approval_rate_max,
            ticket=args.ticket,
            as_json=args.json,
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
    if args.command == "backfill-historical-markets":
        return backfill_historical_markets_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            page_size=args.page_size,
            max_pages=args.max_pages,
            start_cursor=args.start_cursor,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            date_from=parse_date_arg(args.date_from),
            date_to=parse_date_arg(args.date_to),
            as_json=args.json,
        )
    if args.command == "inspect-dataset":
        return inspect_dataset_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-dataset":
        return verify_dataset_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "backfill-research-evidence":
        return backfill_research_evidence_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            source_dataset_id=args.source_dataset_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            limit_markets=args.limit_markets,
            limit_per_source=args.limit_per_source,
            decision_date_from=parse_date_arg(args.decision_date_from),
            decision_date_to=parse_date_arg(args.decision_date_to),
            as_json=args.json,
        )
    if args.command == "inspect-research-corpus":
        return inspect_research_corpus_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            source_dataset_id=args.source_dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-research-alignment":
        return verify_research_alignment_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            source_dataset_id=args.source_dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "backfill-news":
        return backfill_news_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            topic=tuple(args.topic),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            as_json=args.json,
        )
    if args.command == "inspect-news-corpus":
        return inspect_news_corpus_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-news-source":
        return verify_news_source_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            topic=tuple(args.topic),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "backfill-reddit":
        return backfill_reddit_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            subreddit=tuple(args.subreddit),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            max_pages_per_query=args.max_pages_per_query,
            include_comments=args.include_comments,
            comment_limit_per_post=args.comment_limit_per_post,
            incremental=args.incremental,
            as_json=args.json,
        )
    if args.command == "inspect-reddit-corpus":
        return inspect_reddit_corpus_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-reddit-oauth":
        return verify_reddit_oauth_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            subreddit=tuple(args.subreddit),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            include_comments=args.include_comments,
            comment_limit_per_post=args.comment_limit_per_post,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "backfill-x":
        return backfill_x_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            account=tuple(args.account),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            max_pages_per_query=args.max_pages_per_query,
            incremental=args.incremental,
            as_json=args.json,
        )
    if args.command == "inspect-x-corpus":
        return inspect_x_corpus_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-x-source":
        return verify_x_source_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            account=tuple(args.account),
            keyword=tuple(args.keyword),
            limit_per_query=args.limit_per_query,
            corpus_id=args.corpus_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "build-linkage":
        return build_linkage_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            linkage_id=args.linkage_id,
            dataset_id=args.dataset_id,
            news_corpus_id=args.news_corpus_id,
            reddit_corpus_id=args.reddit_corpus_id,
            x_corpus_id=args.x_corpus_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            limit_evidence=args.limit_evidence,
            decision_date_from=parse_date_arg(args.decision_date_from),
            decision_date_to=parse_date_arg(args.decision_date_to),
            as_json=args.json,
        )
    if args.command == "inspect-linkage":
        return inspect_linkage_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            linkage_id=args.linkage_id,
            dataset_id=args.dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "verify-linkage-quality":
        return verify_linkage_quality_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            linkage_id=args.linkage_id,
            dataset_id=args.dataset_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "enrich-alt-data":
        return enrich_alt_data_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            enrichment_id=args.enrichment_id,
            linkage_id=args.linkage_id,
            news_corpus_id=args.news_corpus_id,
            reddit_corpus_id=args.reddit_corpus_id,
            x_corpus_id=args.x_corpus_id,
            checkpoint_path=args.checkpoint_path,
            reset_checkpoint=args.reset_checkpoint,
            limit_records=args.limit_records,
            as_json=args.json,
        )
    if args.command == "inspect-llm-enrichment":
        return inspect_llm_enrichment_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            enrichment_id=args.enrichment_id,
            linkage_id=args.linkage_id,
            checkpoint_path=args.checkpoint_path,
            as_json=args.json,
        )
    if args.command == "build-labels":
        return build_labels_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            corpus_id=args.corpus_id,
            labels_path=args.labels_path,
            as_json=args.json,
        )
    if args.command == "run-benchmarks":
        return run_benchmarks_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            labels_path=args.labels_path,
            split_mode=args.split_mode,
            train_ratio=args.train_ratio,
            validation_ratio=args.validation_ratio,
            train_days=args.train_days,
            validation_days=args.validation_days,
            test_days=args.test_days,
            step_days=args.step_days,
            max_folds=args.max_folds,
            min_confidence=args.min_confidence,
            min_edge_bps=args.min_edge_bps,
            as_json=args.json,
        )
    if args.command == "compare-benchmarks":
        return compare_benchmarks_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_a=args.run_a,
            run_b=args.run_b,
            split=args.split,
            as_json=args.json,
        )
    if args.command == "run-ablation-study":
        return run_ablation_study_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            labels_path=args.labels_path,
            alt_feature_rows_path=args.alt_feature_rows_path,
            split_mode=args.split_mode,
            train_ratio=args.train_ratio,
            validation_ratio=args.validation_ratio,
            train_days=args.train_days,
            validation_days=args.validation_days,
            test_days=args.test_days,
            step_days=args.step_days,
            max_folds=args.max_folds,
            min_confidence=args.min_confidence,
            min_edge_bps=args.min_edge_bps,
            as_json=args.json,
        )
    if args.command == "compare-alt-data-variants":
        return compare_alt_data_variants_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            split=args.split,
            reference_variant=args.reference_variant,
            output_path=args.output,
            as_json=args.json,
        )
    if args.command == "run-walk-forward":
        return run_walk_forward_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            labels_path=args.labels_path,
            baseline_name=args.baseline_name,
            eval_split=args.eval_split,
            train_days=args.train_days,
            validation_days=args.validation_days,
            test_days=args.test_days,
            step_days=args.step_days,
            max_folds=args.max_folds,
            min_confidence=args.min_confidence,
            min_edge_bps=args.min_edge_bps,
            initial_bankroll_usd=args.initial_bankroll_usd,
            base_position_pct=args.base_position_pct,
            max_position_pct=args.max_position_pct,
            min_stake_usd=args.min_stake_usd,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            as_json=args.json,
        )
    if args.command == "generate-strategy-report":
        return generate_strategy_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            output_path=args.output,
            as_json=args.json,
        )
    if args.command == "build-feature-dataset":
        return build_feature_dataset_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            corpus_id=args.corpus_id,
            as_json=args.json,
        )
    if args.command == "inspect-feature-schema":
        return inspect_feature_schema_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            as_json=args.json,
        )
    if args.command == "verify-feature-parity":
        return verify_feature_parity_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            as_json=args.json,
        )
    if args.command == "build-alt-feature-dataset":
        return build_alt_feature_dataset_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            linkage_id=args.linkage_id,
            news_corpus_id=args.news_corpus_id,
            reddit_corpus_id=args.reddit_corpus_id,
            x_corpus_id=args.x_corpus_id,
            enrichment_id=args.enrichment_id,
            as_json=args.json,
        )
    if args.command == "inspect-alt-feature-schema":
        return inspect_alt_feature_schema_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            as_json=args.json,
        )
    if args.command == "verify-alt-feature-parity":
        return verify_alt_feature_parity_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            as_json=args.json,
        )
    if args.command == "train-baseline-models":
        return train_baseline_models_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            feature_rows_path=args.feature_rows_path,
            split_mode=args.split_mode,
            train_ratio=args.train_ratio,
            validation_ratio=args.validation_ratio,
            window_days=args.window_days,
            include_xgboost=args.include_xgboost,
            as_json=args.json,
        )
    if args.command == "calibrate-model":
        return calibrate_model_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            model_name=args.model_name,
            method=args.method,
            fit_split=args.fit_split,
            eval_split=args.eval_split,
            as_json=args.json,
        )
    if args.command == "compare-models":
        return compare_models_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            split=args.split,
            as_json=args.json,
        )
    if args.command == "generate-model-card":
        return generate_model_card_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            dataset_id=args.dataset_id,
            run_id=args.run_id,
            model_name=args.model_name,
            calibration_run_id=args.calibration_run_id,
            output_path=args.output,
            owner=args.owner,
            decision=args.decision,
            as_json=args.json,
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
