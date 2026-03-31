"""Strategy research commands -- re-exports for backward compatibility.

Actual implementations live in:
- strategy_data_commands.py
- strategy_corpus_commands.py
- strategy_features_commands.py
- strategy_training_commands.py
- strategy_eval_commands.py
"""
from __future__ import annotations

from prediction_market_bot.cli.commands.strategy_data_commands import (
    backfill_historical_markets_command,
    inspect_dataset_command,
    verify_dataset_command,
)
from prediction_market_bot.cli.commands.strategy_corpus_commands import (
    backfill_news_command,
    backfill_reddit_command,
    backfill_research_evidence_command,
    backfill_x_command,
    inspect_news_corpus_command,
    inspect_reddit_corpus_command,
    inspect_research_corpus_command,
    inspect_x_corpus_command,
    verify_news_source_command,
    verify_reddit_oauth_command,
    verify_research_alignment_command,
    verify_x_source_command,
)
from prediction_market_bot.cli.commands.strategy_features_commands import (
    build_alt_feature_dataset_command,
    build_feature_dataset_command,
    build_linkage_command,
    compare_alt_data_variants_command,
    enrich_alt_data_command,
    inspect_alt_feature_schema_command,
    inspect_feature_schema_command,
    inspect_linkage_command,
    inspect_llm_enrichment_command,
    verify_alt_feature_parity_command,
    verify_feature_parity_command,
    verify_linkage_quality_command,
)
from prediction_market_bot.cli.commands.strategy_training_commands import (
    calibrate_model_command,
    compare_models_command,
    generate_model_card_command,
    train_baseline_models_command,
)
from prediction_market_bot.cli.commands.strategy_eval_commands import (
    build_labels_command,
    compare_benchmarks_command,
    generate_strategy_report_command,
    run_ablation_study_command,
    run_benchmarks_command,
    run_walk_forward_command,
)

__all__ = [
    "backfill_historical_markets_command",
    "backfill_news_command",
    "backfill_reddit_command",
    "backfill_research_evidence_command",
    "backfill_x_command",
    "build_alt_feature_dataset_command",
    "build_feature_dataset_command",
    "build_labels_command",
    "build_linkage_command",
    "calibrate_model_command",
    "compare_alt_data_variants_command",
    "compare_benchmarks_command",
    "compare_models_command",
    "enrich_alt_data_command",
    "generate_model_card_command",
    "generate_strategy_report_command",
    "inspect_alt_feature_schema_command",
    "inspect_dataset_command",
    "inspect_feature_schema_command",
    "inspect_linkage_command",
    "inspect_llm_enrichment_command",
    "inspect_news_corpus_command",
    "inspect_reddit_corpus_command",
    "inspect_research_corpus_command",
    "inspect_x_corpus_command",
    "run_ablation_study_command",
    "run_benchmarks_command",
    "run_walk_forward_command",
    "train_baseline_models_command",
    "verify_alt_feature_parity_command",
    "verify_dataset_command",
    "verify_feature_parity_command",
    "verify_linkage_quality_command",
    "verify_news_source_command",
    "verify_reddit_oauth_command",
    "verify_research_alignment_command",
    "verify_x_source_command",
]
