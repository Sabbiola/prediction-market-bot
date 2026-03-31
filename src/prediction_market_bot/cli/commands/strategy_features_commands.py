from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.strategy_research.alt_features import AltFeatureDatasetBuilderService
from prediction_market_bot.strategy_research.features import FeatureDatasetBuilderService
from prediction_market_bot.strategy_research.linkage import EvidenceMarketLinkageService
from prediction_market_bot.strategy_research.llm_enrichment import (
    AltDataLlmEnrichmentService,
    DeterministicEnrichmentProvider,
    ExternalLlmProviderStub,
    LlmEnrichmentProvider,
)

logger = logging.getLogger(__name__)


def _build_linkage_service(config_path: Path, agents_config_path: Path) -> EvidenceMarketLinkageService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return EvidenceMarketLinkageService(
        base_dir=Path(sr.linkage_base_dir),
        historical_base_dir=Path(sr.base_dir),
        news_corpus_base_dir=Path(sr.news_corpus_base_dir),
        reddit_corpus_base_dir=Path(sr.reddit_corpus_base_dir),
        x_corpus_base_dir=Path(sr.x_corpus_base_dir),
        default_linkage_id=sr.linkage_default_linkage_id,
        default_dataset_id=sr.linkage_source_dataset_id or sr.default_dataset_id,
        default_news_corpus_id=sr.linkage_news_corpus_id or sr.news_corpus_default_corpus_id,
        default_reddit_corpus_id=sr.linkage_reddit_corpus_id or sr.reddit_corpus_default_corpus_id,
        default_x_corpus_id=sr.linkage_x_corpus_id or sr.x_corpus_default_corpus_id,
        enabled_source_classes=sr.linkage_enabled_source_classes,
        max_staleness_days=sr.linkage_max_staleness_days,
        similarity_threshold=sr.linkage_similarity_threshold,
        ambiguity_margin=sr.linkage_ambiguity_margin,
        max_candidates_per_evidence=sr.linkage_max_candidates_per_evidence,
        alias_map=sr.linkage_alias_map,
    )


def _build_llm_enrichment_service(config_path: Path, agents_config_path: Path) -> AltDataLlmEnrichmentService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research

    primary_provider_name = sr.llm_enrichment_provider.strip().lower() or "deterministic"
    primary_provider: LlmEnrichmentProvider
    if primary_provider_name == "deterministic":
        primary_provider = DeterministicEnrichmentProvider(model_id=sr.llm_enrichment_model_id)
    else:
        if not sr.llm_enrichment_allow_external_provider:
            raise ValueError(
                "strategy_research.llm_enrichment.provider is set to an external mode, "
                "but allow_external_provider=false."
            )
        primary_provider = ExternalLlmProviderStub(model_id=sr.llm_enrichment_model_id)

    fallback_provider: LlmEnrichmentProvider | None = None
    if sr.llm_enrichment_enable_fallback:
        fallback_provider = DeterministicEnrichmentProvider(model_id="deterministic-enrichment-v1")

    return AltDataLlmEnrichmentService(
        base_dir=Path(sr.llm_enrichment_base_dir),
        linkage_base_dir=Path(sr.linkage_base_dir),
        news_corpus_base_dir=Path(sr.news_corpus_base_dir),
        reddit_corpus_base_dir=Path(sr.reddit_corpus_base_dir),
        x_corpus_base_dir=Path(sr.x_corpus_base_dir),
        default_enrichment_id=sr.llm_enrichment_default_enrichment_id,
        default_linkage_id=sr.llm_enrichment_default_linkage_id or sr.linkage_default_linkage_id,
        default_news_corpus_id=sr.llm_enrichment_news_corpus_id or sr.news_corpus_default_corpus_id,
        default_reddit_corpus_id=sr.llm_enrichment_reddit_corpus_id or sr.reddit_corpus_default_corpus_id,
        default_x_corpus_id=sr.llm_enrichment_x_corpus_id or sr.x_corpus_default_corpus_id,
        enabled=sr.llm_enrichment_enabled,
        prompt_version=sr.llm_enrichment_prompt_version,
        include_states=sr.llm_enrichment_include_states,
        throttle_sec=sr.llm_enrichment_throttle_sec,
        primary_provider=primary_provider,
        fallback_provider=fallback_provider,
    )


def _build_feature_service(config_path: Path, agents_config_path: Path) -> FeatureDatasetBuilderService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return FeatureDatasetBuilderService(
        historical_base_dir=Path(sr.base_dir),
        research_corpus_base_dir=Path(sr.research_corpus_base_dir),
        default_dataset_id=sr.default_dataset_id,
        default_corpus_id=sr.research_corpus_default_corpus_id,
    )


def _build_alt_feature_service(config_path: Path, agents_config_path: Path) -> AltFeatureDatasetBuilderService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return AltFeatureDatasetBuilderService(
        historical_base_dir=Path(sr.base_dir),
        linkage_base_dir=Path(sr.linkage_base_dir),
        news_corpus_base_dir=Path(sr.news_corpus_base_dir),
        reddit_corpus_base_dir=Path(sr.reddit_corpus_base_dir),
        x_corpus_base_dir=Path(sr.x_corpus_base_dir),
        llm_enrichment_base_dir=Path(sr.llm_enrichment_base_dir),
        default_dataset_id=sr.default_dataset_id,
        default_linkage_id=sr.linkage_default_linkage_id,
        default_news_corpus_id=sr.news_corpus_default_corpus_id,
        default_reddit_corpus_id=sr.reddit_corpus_default_corpus_id,
        default_x_corpus_id=sr.x_corpus_default_corpus_id,
        default_enrichment_id=sr.llm_enrichment_default_enrichment_id,
    )


def build_linkage_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    linkage_id: str | None,
    dataset_id: str | None,
    news_corpus_id: str | None,
    reddit_corpus_id: str | None,
    x_corpus_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    limit_evidence: int | None,
    decision_date_from: date | None,
    decision_date_to: date | None,
    as_json: bool,
) -> int:
    service = _build_linkage_service(config_path, agents_config_path)
    summary = service.build_linkage(
        linkage_id=linkage_id,
        dataset_id=dataset_id,
        news_corpus_id=news_corpus_id,
        reddit_corpus_id=reddit_corpus_id,
        x_corpus_id=x_corpus_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        limit_evidence=limit_evidence,
        decision_date_from=decision_date_from,
        decision_date_to=decision_date_to,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Evidence linkage build completed "
            f"linkage_id={summary.linkage_id} dataset_id={summary.dataset_id} "
            f"evidence_processed={summary.evidence_processed} evidence_skipped={summary.evidence_skipped} "
            f"linked={summary.linked_count} ambiguous={summary.ambiguous_count} "
            f"unresolved={summary.unresolved_count} stale={summary.stale_count}"
        )
        print(f"Linkage root: {summary.linkage_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_linkage_built",
        extra={
            "event": "strategy_research_linkage_built",
            **payload,
        },
    )
    return 0


def inspect_linkage_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    linkage_id: str | None,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_linkage_service(config_path, agents_config_path)
    inspection = service.inspect_linkage(
        linkage_id=linkage_id,
        dataset_id=dataset_id,
        checkpoint_path=checkpoint_path,
    )
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Linkage id: {inspection.linkage_id}")
        print(f"Dataset id: {inspection.dataset_id}")
        print(f"Root: {inspection.linkage_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed evidence: {inspection.processed_evidence_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
        print("State counts:")
        for key, value in inspection.state_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "strategy_research_linkage_inspected",
        extra={
            "event": "strategy_research_linkage_inspected",
            **payload,
        },
    )
    return 0


def verify_linkage_quality_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    linkage_id: str | None,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_linkage_service(config_path, agents_config_path)
    verification = service.verify_linkage_quality(
        linkage_id=linkage_id,
        dataset_id=dataset_id,
        checkpoint_path=checkpoint_path,
    )
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Linkage quality verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "strategy_research_linkage_verified",
        extra={
            "event": "strategy_research_linkage_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1


def enrich_alt_data_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    enrichment_id: str | None,
    linkage_id: str | None,
    news_corpus_id: str | None,
    reddit_corpus_id: str | None,
    x_corpus_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    limit_records: int | None,
    as_json: bool,
) -> int:
    service = _build_llm_enrichment_service(config_path, agents_config_path)
    summary = service.enrich_alt_data(
        enrichment_id=enrichment_id,
        linkage_id=linkage_id,
        news_corpus_id=news_corpus_id,
        reddit_corpus_id=reddit_corpus_id,
        x_corpus_id=x_corpus_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        limit_records=limit_records,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "LLM enrichment completed "
            f"enrichment_id={summary.enrichment_id} linkage_id={summary.linkage_id} "
            f"records_processed={summary.records_processed} failures={summary.failures} "
            f"fallbacks_used={summary.fallbacks_used} schema_violations={summary.schema_violations}"
        )
        print(f"Enrichment root: {summary.enrichment_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_llm_enrichment_completed",
        extra={
            "event": "strategy_research_llm_enrichment_completed",
            **payload,
        },
    )
    return 0


def inspect_llm_enrichment_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    enrichment_id: str | None,
    linkage_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_llm_enrichment_service(config_path, agents_config_path)
    inspection = service.inspect_enrichment(
        enrichment_id=enrichment_id,
        linkage_id=linkage_id,
        checkpoint_path=checkpoint_path,
    )
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Enrichment: {inspection.enrichment_id}")
        print(f"Linkage id: {inspection.linkage_id}")
        print(f"Root: {inspection.enrichment_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed evidence count: {inspection.processed_evidence_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
        print("Status counts:")
        for key, value in inspection.status_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "strategy_research_llm_enrichment_inspected",
        extra={
            "event": "strategy_research_llm_enrichment_inspected",
            **payload,
        },
    )
    return 0


def build_feature_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    corpus_id: str | None,
    as_json: bool,
) -> int:
    service = _build_feature_service(config_path, agents_config_path)
    summary = service.build_feature_dataset(
        dataset_id=dataset_id,
        corpus_id=corpus_id,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Feature dataset build completed "
            f"dataset_id={summary.dataset_id} corpus_id={summary.corpus_id} "
            f"schema_version={summary.schema_version} rows_written={summary.rows_written}"
        )
        print(f"Rows path: {summary.rows_path}")
        print(f"Schema path: {summary.schema_path}")
        print(f"Manifest path: {summary.manifest_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_feature_dataset_built",
        extra={
            "event": "strategy_research_feature_dataset_built",
            **payload,
        },
    )
    return 0


def inspect_feature_schema_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    as_json: bool,
) -> int:
    service = _build_feature_service(config_path, agents_config_path)
    inspection = service.inspect_feature_schema(dataset_id=dataset_id)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Dataset: {inspection.dataset_id}")
        print(f"Schema version: {inspection.schema_version}")
        print(f"Schema path: {inspection.schema_path}")
        print(f"Rows path: {inspection.rows_path}")
        print(f"Manifest path: {inspection.manifest_path}")
        print(f"Schema exists: {inspection.schema_exists}")
        print(f"Rows exists: {inspection.rows_exist}")
        print(f"Rows count: {inspection.rows_count}")
        print("Columns:")
        for column in inspection.columns:
            print(f"- {column.name} ({column.dtype}) [{column.group}]")
    logger.info(
        "strategy_research_feature_schema_inspected",
        extra={
            "event": "strategy_research_feature_schema_inspected",
            **payload,
        },
    )
    return 0


def verify_feature_parity_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    as_json: bool,
) -> int:
    service = _build_feature_service(config_path, agents_config_path)
    verification = service.verify_feature_parity(dataset_id=dataset_id)
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Feature schema parity verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "strategy_research_feature_parity_verified",
        extra={
            "event": "strategy_research_feature_parity_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1


def build_alt_feature_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    linkage_id: str | None,
    news_corpus_id: str | None,
    reddit_corpus_id: str | None,
    x_corpus_id: str | None,
    enrichment_id: str | None,
    as_json: bool,
) -> int:
    service = _build_alt_feature_service(config_path, agents_config_path)
    summary = service.build_alt_feature_dataset(
        dataset_id=dataset_id,
        linkage_id=linkage_id,
        news_corpus_id=news_corpus_id,
        reddit_corpus_id=reddit_corpus_id,
        x_corpus_id=x_corpus_id,
        enrichment_id=enrichment_id,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Alt feature dataset build completed "
            f"dataset_id={summary.dataset_id} linkage_id={summary.linkage_id} "
            f"enrichment_id={summary.enrichment_id} schema_version={summary.schema_version} "
            f"rows_written={summary.rows_written}"
        )
        print(f"Rows path: {summary.rows_path}")
        print(f"Schema path: {summary.schema_path}")
        print(f"Manifest path: {summary.manifest_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_alt_feature_dataset_built",
        extra={
            "event": "strategy_research_alt_feature_dataset_built",
            **payload,
        },
    )
    return 0


def inspect_alt_feature_schema_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    as_json: bool,
) -> int:
    service = _build_alt_feature_service(config_path, agents_config_path)
    inspection = service.inspect_alt_feature_schema(dataset_id=dataset_id)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Dataset: {inspection.dataset_id}")
        print(f"Schema version: {inspection.schema_version}")
        print(f"Schema path: {inspection.schema_path}")
        print(f"Rows path: {inspection.rows_path}")
        print(f"Manifest path: {inspection.manifest_path}")
        print(f"Schema exists: {inspection.schema_exists}")
        print(f"Rows exists: {inspection.rows_exist}")
        print(f"Rows count: {inspection.rows_count}")
        print("Columns:")
        for column in inspection.columns:
            print(f"- {column.name} ({column.dtype}) [{column.group}]")
    logger.info(
        "strategy_research_alt_feature_schema_inspected",
        extra={
            "event": "strategy_research_alt_feature_schema_inspected",
            **payload,
        },
    )
    return 0


def verify_alt_feature_parity_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    as_json: bool,
) -> int:
    service = _build_alt_feature_service(config_path, agents_config_path)
    verification = service.verify_alt_feature_parity(dataset_id=dataset_id)
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Alt feature schema parity verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "strategy_research_alt_feature_parity_verified",
        extra={
            "event": "strategy_research_alt_feature_parity_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1


def compare_alt_data_variants_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    split: str,
    reference_variant: str,
    output_path: Path | None,
    as_json: bool,
) -> int:
    from prediction_market_bot.cli.commands.strategy_eval_commands import _build_benchmark_service

    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.compare_alt_data_variants(
        dataset_id=dataset_id,
        run_id=run_id,
        split=split,
        reference_variant=reference_variant,
        output_path=output_path,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Alt-data variant comparison completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"split={summary.split} reference_variant={summary.reference_variant}"
        )
        print(f"Report path: {summary.report_path}")
        for variant in summary.ranking:
            delta = summary.variant_deltas.get(variant, {})
            print(
                f"- {variant}: "
                f"delta_brier={float(delta.get('delta_brier_score', 0.0)):+.6f} "
                f"delta_log_loss={float(delta.get('delta_log_loss', 0.0)):+.6f} "
                f"delta_cal={float(delta.get('delta_calibration_error', 0.0)):+.6f} "
                f"delta_approval={float(delta.get('delta_approval_rate', 0.0)):+.4f} "
                f"delta_edge_capture={float(delta.get('delta_edge_capture_ratio', 0.0)):+.6f}"
            )
    logger.info(
        "strategy_research_alt_data_variants_compared",
        extra={
            "event": "strategy_research_alt_data_variants_compared",
            **payload,
        },
    )
    return 0
