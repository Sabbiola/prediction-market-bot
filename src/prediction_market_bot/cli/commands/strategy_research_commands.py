from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_http_client
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.secrets import build_secret_provider
from prediction_market_bot.infrastructure import build_live_research_sources
from prediction_market_bot.infrastructure.alt_data import (
    AltDataAdapterRegistry,
    GoogleNewsRssAdapter,
    RedditOAuthAdapter,
    SourceOperation,
    XApiAdapter,
)
from prediction_market_bot.strategy_research.benchmarking import StrategyResearchBenchmarkService
from prediction_market_bot.strategy_research.data_ingest import (
    HistoricalDataIngestionService,
    HistoricalResolvedMarketProvider,
)
from prediction_market_bot.strategy_research.alt_features import AltFeatureDatasetBuilderService
from prediction_market_bot.strategy_research.features import FeatureDatasetBuilderService
from prediction_market_bot.strategy_research.linkage import EvidenceMarketLinkageService
from prediction_market_bot.strategy_research.llm_enrichment import (
    AltDataLlmEnrichmentService,
    DeterministicEnrichmentProvider,
    ExternalLlmProviderStub,
    LlmEnrichmentProvider,
)
from prediction_market_bot.strategy_research.news_corpus import NewsCorpusService
from prediction_market_bot.strategy_research.reddit_corpus import RedditCorpusService
from prediction_market_bot.strategy_research.research_corpus import ResearchEvidenceArchivalService
from prediction_market_bot.strategy_research.simulator import WalkForwardStrategyService
from prediction_market_bot.strategy_research.training import StrategyTrainingLabService
from prediction_market_bot.strategy_research.x_corpus import XCorpusService

logger = logging.getLogger(__name__)


def _build_historical_service(config_path: Path, agents_config_path: Path) -> HistoricalDataIngestionService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    http_client = build_http_client(settings)
    provider = HistoricalResolvedMarketProvider(
        http_client=http_client,
        markets_endpoint_url=sr.markets_endpoint_url,
        events_endpoint_url=sr.events_endpoint_url,
        snapshots_endpoint_url=sr.snapshots_endpoint_url,
        orderbook_endpoint_url=sr.orderbook_endpoint_url,
        trades_endpoint_url=sr.trades_endpoint_url,
        resolutions_endpoint_url=sr.resolutions_endpoint_url,
        include_orderbook=sr.include_orderbook,
        include_trades=sr.include_trades,
        page_size=sr.page_size,
        throttle_sec=sr.throttle_sec,
    )
    return HistoricalDataIngestionService(
        base_dir=Path(sr.base_dir),
        provider=provider,
        default_dataset_id=sr.default_dataset_id,
        default_page_size=sr.page_size,
        default_max_pages_per_run=sr.max_pages_per_run,
    )


def _build_research_corpus_service(config_path: Path, agents_config_path: Path) -> ResearchEvidenceArchivalService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    http_client = build_http_client(settings)
    enabled_sources = sr.research_corpus_enabled_sources if sr.research_corpus_enabled_sources else None
    sources = build_live_research_sources(
        wikipedia_endpoint_url=settings.live_research.wikipedia_endpoint_url,
        openalex_endpoint_url=settings.live_research.openalex_endpoint_url,
        timeout_sec=settings.http.timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        cache_ttl_seconds=settings.http.cache_ttl_sec,
        wikipedia_timeout_sec=settings.live_research.wikipedia_timeout_sec,
        wikipedia_max_retries=settings.live_research.wikipedia_max_retries,
        wikipedia_retry_backoff_sec=settings.live_research.wikipedia_retry_backoff_sec,
        wikipedia_retry_jitter_sec=settings.live_research.wikipedia_retry_jitter_sec,
        wikipedia_cache_ttl_seconds=settings.live_research.wikipedia_cache_ttl_sec,
        wikipedia_headers=settings.live_research.wikipedia_headers or {},
        wikipedia_api_key_header=settings.live_research.wikipedia_api_key_header,
        wikipedia_api_key_prefix=settings.live_research.wikipedia_api_key_prefix,
        wikipedia_require_api_key=settings.live_research.wikipedia_require_api_key,
        openalex_timeout_sec=settings.live_research.openalex_timeout_sec,
        openalex_max_retries=settings.live_research.openalex_max_retries,
        openalex_retry_backoff_sec=settings.live_research.openalex_retry_backoff_sec,
        openalex_retry_jitter_sec=settings.live_research.openalex_retry_jitter_sec,
        openalex_cache_ttl_seconds=settings.live_research.openalex_cache_ttl_sec,
        openalex_headers=settings.live_research.openalex_headers or {},
        openalex_api_key_header=settings.live_research.openalex_api_key_header,
        openalex_api_key_prefix=settings.live_research.openalex_api_key_prefix,
        openalex_require_api_key=settings.live_research.openalex_require_api_key,
        enabled_sources=enabled_sources,
        http_client=http_client,
    )
    return ResearchEvidenceArchivalService(
        base_dir=Path(sr.research_corpus_base_dir),
        source_dataset_base_dir=Path(sr.base_dir),
        default_corpus_id=sr.research_corpus_default_corpus_id,
        default_source_dataset_id=sr.research_corpus_source_dataset_id or sr.default_dataset_id,
        sources=sources,
        default_limit_per_source=sr.research_corpus_limit_per_source,
        throttle_sec=sr.research_corpus_throttle_sec,
        require_published_at=sr.research_corpus_require_published_at,
        drop_unaligned=sr.research_corpus_drop_unaligned,
    )


def _build_news_corpus_service(config_path: Path, agents_config_path: Path) -> NewsCorpusService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    if not settings.alt_data.enabled:
        raise ValueError(
            "alt_data.enabled=false. Enable alt_data and configure source 'news_rss_web' before backfill-news."
        )
    secrets = build_secret_provider(settings)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=lambda env_name: secrets.get(env_name),
    )
    configured_source = registry.require_source_ready(
        settings.strategy_research.news_corpus_default_source_id,
        operation=SourceOperation.BACKFILL,
        require_enabled=True,
    )
    configured_source.ensure_operation_supported(SourceOperation.SEARCH)
    registration = configured_source.registration
    if registration.adapter != "rss_news_adapter":
        raise ValueError(
            "Unsupported adapter for news corpus source. "
            f"source_id={registration.source_id} adapter={registration.adapter} expected=rss_news_adapter"
        )

    adapter = GoogleNewsRssAdapter(
        source_id=registration.source_id,
        source_class=registration.source_class,
        source_name=registration.adapter,
        endpoint_url=registration.endpoint_url,
        timeout_sec=settings.http.timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        cache_ttl_sec=settings.http.cache_ttl_sec,
        language=settings.strategy_research.news_corpus_language,
        region=settings.strategy_research.news_corpus_region,
    )
    return NewsCorpusService(
        base_dir=Path(settings.strategy_research.news_corpus_base_dir),
        default_corpus_id=settings.strategy_research.news_corpus_default_corpus_id,
        source_id=registration.source_id,
        adapter=adapter,
        default_limit_per_query=settings.strategy_research.news_corpus_limit_per_query,
        throttle_sec=settings.strategy_research.news_corpus_throttle_sec,
    )


def _build_reddit_corpus_service(config_path: Path, agents_config_path: Path) -> RedditCorpusService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    if not settings.alt_data.enabled:
        raise ValueError(
            "alt_data.enabled=false. Enable alt_data and configure source 'reddit' before backfill-reddit."
        )
    secrets = build_secret_provider(settings)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=lambda env_name: secrets.get(env_name),
    )
    configured_source = registry.require_source_ready(
        settings.strategy_research.reddit_corpus_default_source_id,
        operation=SourceOperation.BACKFILL,
        require_enabled=True,
    )
    configured_source.ensure_operation_supported(SourceOperation.SEARCH)
    registration = configured_source.registration
    if registration.adapter != "reddit_oauth_adapter":
        raise ValueError(
            "Unsupported adapter for reddit corpus source. "
            f"source_id={registration.source_id} adapter={registration.adapter} expected=reddit_oauth_adapter"
        )
    oauth_token = secrets.get(registration.credential_env)
    adapter = RedditOAuthAdapter(
        source_id=registration.source_id,
        source_class=registration.source_class,
        source_name=registration.adapter,
        endpoint_url=registration.endpoint_url,
        oauth_token=oauth_token,
        user_agent=settings.http.user_agent_with_contact,
        timeout_sec=settings.http.timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
    )
    return RedditCorpusService(
        base_dir=Path(settings.strategy_research.reddit_corpus_base_dir),
        default_corpus_id=settings.strategy_research.reddit_corpus_default_corpus_id,
        source_id=registration.source_id,
        adapter=adapter,
        default_limit_per_query=settings.strategy_research.reddit_corpus_limit_per_query,
        default_comment_limit_per_post=settings.strategy_research.reddit_corpus_comment_limit_per_post,
        default_max_pages_per_query=settings.strategy_research.reddit_corpus_max_pages_per_query,
        default_include_comments=settings.strategy_research.reddit_corpus_include_comments,
        throttle_sec=settings.strategy_research.reddit_corpus_throttle_sec,
    )


def _build_x_corpus_service(config_path: Path, agents_config_path: Path) -> XCorpusService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    if not settings.alt_data.enabled:
        raise ValueError(
            "alt_data.enabled=false. Enable alt_data and configure source 'x' before backfill-x."
        )
    auth_mode = settings.strategy_research.x_corpus_auth_mode.strip().lower() or "bearer"
    user_context_available = auth_mode == "user_context"
    secrets = build_secret_provider(settings)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=lambda env_name: secrets.get(env_name),
        user_context_resolver=lambda _source_id: user_context_available,
    )
    configured_source = registry.require_source_ready(
        settings.strategy_research.x_corpus_default_source_id,
        operation=SourceOperation.BACKFILL,
        require_enabled=True,
    )
    configured_source.ensure_operation_supported(SourceOperation.SEARCH)
    registration = configured_source.registration
    if registration.adapter != "x_api_adapter":
        raise ValueError(
            "Unsupported adapter for x corpus source. "
            f"source_id={registration.source_id} adapter={registration.adapter} expected=x_api_adapter"
        )
    auth_token = secrets.get(registration.credential_env)
    adapter = XApiAdapter(
        source_id=registration.source_id,
        source_class=registration.source_class,
        source_name=registration.adapter,
        endpoint_url=registration.endpoint_url,
        auth_token=auth_token,
        auth_mode=auth_mode,
        search_endpoint_path=settings.strategy_research.x_corpus_search_endpoint_path,
        user_lookup_endpoint_path_template=settings.strategy_research.x_corpus_user_lookup_endpoint_path_template,
        user_posts_endpoint_path_template=settings.strategy_research.x_corpus_user_posts_endpoint_path_template,
        user_agent=settings.http.user_agent_with_contact,
        timeout_sec=settings.http.timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
    )
    return XCorpusService(
        base_dir=Path(settings.strategy_research.x_corpus_base_dir),
        default_corpus_id=settings.strategy_research.x_corpus_default_corpus_id,
        source_id=registration.source_id,
        adapter=adapter,
        default_limit_per_query=settings.strategy_research.x_corpus_limit_per_query,
        default_max_pages_per_query=settings.strategy_research.x_corpus_max_pages_per_query,
        throttle_sec=settings.strategy_research.x_corpus_throttle_sec,
    )


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


def _build_benchmark_service(config_path: Path, agents_config_path: Path) -> StrategyResearchBenchmarkService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return StrategyResearchBenchmarkService(
        historical_base_dir=Path(sr.base_dir),
        research_corpus_base_dir=Path(sr.research_corpus_base_dir),
        default_dataset_id=sr.default_dataset_id,
        default_corpus_id=sr.research_corpus_default_corpus_id,
        prediction_settings=settings.prediction,
    )


def _build_walk_forward_service(config_path: Path, agents_config_path: Path) -> WalkForwardStrategyService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return WalkForwardStrategyService(
        historical_base_dir=Path(sr.base_dir),
        default_dataset_id=sr.default_dataset_id,
        prediction_settings=settings.prediction,
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


def _build_training_service(config_path: Path, agents_config_path: Path) -> StrategyTrainingLabService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    return StrategyTrainingLabService(
        historical_base_dir=Path(sr.base_dir),
        default_dataset_id=sr.default_dataset_id,
        model_card_template_path=Path("docs/MODEL_CARD_TEMPLATE.md"),
    )


def backfill_historical_markets_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    page_size: int | None,
    max_pages: int | None,
    start_cursor: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    date_from: date | None,
    date_to: date | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    summary = service.backfill_historical_markets(
        dataset_id=dataset_id,
        page_size=page_size,
        max_pages=max_pages,
        start_cursor=start_cursor,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        date_from=date_from,
        date_to=date_to,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Historical backfill completed "
            f"dataset_id={summary.dataset_id} pages={summary.pages_processed} "
            f"markets_processed={summary.markets_processed} markets_skipped={summary.markets_skipped} "
            f"checkpoint_completed={summary.checkpoint_completed} next_cursor={summary.next_cursor or 'none'}"
        )
        print(f"Dataset root: {summary.dataset_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "historical_markets_backfill_command_completed",
        extra={
            "event": "historical_markets_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    inspection = service.inspect_dataset(dataset_id=dataset_id, checkpoint_path=checkpoint_path)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Dataset: {inspection.dataset_id}")
        print(f"Root: {inspection.dataset_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Next cursor: {inspection.checkpoint_cursor or 'none'}")
        print(f"Processed markets: {inspection.processed_market_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "historical_dataset_inspected",
        extra={
            "event": "historical_dataset_inspected",
            **payload,
        },
    )
    return 0


def verify_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    verification = service.verify_dataset(dataset_id=dataset_id, checkpoint_path=checkpoint_path)
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Dataset verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "historical_dataset_verified",
        extra={
            "event": "historical_dataset_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1


def backfill_research_evidence_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    source_dataset_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    limit_markets: int | None,
    limit_per_source: int | None,
    decision_date_from: date | None,
    decision_date_to: date | None,
    as_json: bool,
) -> int:
    service = _build_research_corpus_service(config_path, agents_config_path)
    summary = service.backfill_research_evidence(
        corpus_id=corpus_id,
        source_dataset_id=source_dataset_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        limit_markets=limit_markets,
        limit_per_source=limit_per_source,
        decision_date_from=decision_date_from,
        decision_date_to=decision_date_to,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Research evidence backfill completed "
            f"corpus_id={summary.corpus_id} source_dataset_id={summary.source_dataset_id} "
            f"decision_points_processed={summary.decision_points_processed} "
            f"normalized_findings_persisted={summary.normalized_findings_persisted} "
            f"checkpoint_completed={summary.checkpoint_completed}"
        )
        print(f"Corpus root: {summary.corpus_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "research_evidence_backfill_command_completed",
        extra={
            "event": "research_evidence_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_research_corpus_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    source_dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_research_corpus_service(config_path, agents_config_path)
    inspection = service.inspect_corpus(
        corpus_id=corpus_id,
        source_dataset_id=source_dataset_id,
        checkpoint_path=checkpoint_path,
    )
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Corpus: {inspection.corpus_id}")
        print(f"Source dataset: {inspection.source_dataset_id}")
        print(f"Root: {inspection.corpus_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed markets: {inspection.processed_market_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "research_corpus_inspected",
        extra={
            "event": "research_corpus_inspected",
            **payload,
        },
    )
    return 0


def verify_research_alignment_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    source_dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_research_corpus_service(config_path, agents_config_path)
    verification = service.verify_alignment(
        corpus_id=corpus_id,
        source_dataset_id=source_dataset_id,
        checkpoint_path=checkpoint_path,
    )
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Research alignment verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "research_alignment_verified",
        extra={
            "event": "research_alignment_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1


def backfill_news_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    topic: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    as_json: bool,
) -> int:
    service = _build_news_corpus_service(config_path, agents_config_path)
    summary = service.backfill_news(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        topics=topic,
        keywords=keyword,
        limit_per_query=limit_per_query,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "News backfill completed "
            f"corpus_id={summary.corpus_id} source_id={summary.source_id} "
            f"query_count={summary.query_count} queries_processed={summary.queries_processed} "
            f"queries_skipped={summary.queries_skipped} source_failures={summary.source_failures}"
        )
        print(f"Corpus root: {summary.corpus_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "news_corpus_backfill_command_completed",
        extra={
            "event": "news_corpus_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_news_corpus_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_news_corpus_service(config_path, agents_config_path)
    inspection = service.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Corpus: {inspection.corpus_id}")
        print(f"Source: {inspection.source_id}")
        print(f"Root: {inspection.corpus_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed queries: {inspection.processed_query_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "news_corpus_inspected",
        extra={
            "event": "news_corpus_inspected",
            **payload,
        },
    )
    return 0


def verify_news_source_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    topic: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_news_corpus_service(config_path, agents_config_path)
    source_verification = service.verify_source(
        topics=topic,
        keywords=keyword,
        limit_per_query=limit_per_query,
    )
    corpus_verification = service.verify_corpus(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
    )
    payload = {
        "source_verification": source_verification.to_dict(),
        "corpus_verification": corpus_verification.to_dict(),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        source_status = "OK" if source_verification.ok else "FAIL"
        corpus_status = "OK" if corpus_verification.ok else "FAIL"
        print(f"News source verification: {source_status}")
        print(f"News corpus verification: {corpus_status}")
        for error in source_verification.errors:
            print(f"SOURCE ERROR: {error}")
        for warning in source_verification.warnings:
            print(f"SOURCE WARN: {warning}")
        for error in corpus_verification.errors:
            print(f"CORPUS ERROR: {error}")
        for warning in corpus_verification.warnings:
            print(f"CORPUS WARN: {warning}")
    logger.info(
        "news_source_verified",
        extra={
            "event": "news_source_verified",
            "source_ok": source_verification.ok,
            "corpus_ok": corpus_verification.ok,
            "source_id": source_verification.source_id,
            "fetched_articles": source_verification.fetched_articles,
            "source_error_count": len(source_verification.errors),
            "corpus_error_count": len(corpus_verification.errors),
        },
    )
    return 0 if source_verification.ok and corpus_verification.ok else 1


def backfill_reddit_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    subreddit: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    max_pages_per_query: int | None,
    include_comments: bool | None,
    comment_limit_per_post: int | None,
    incremental: bool,
    as_json: bool,
) -> int:
    service = _build_reddit_corpus_service(config_path, agents_config_path)
    summary = service.backfill_reddit(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        subreddits=subreddit,
        keywords=keyword,
        limit_per_query=limit_per_query,
        max_pages_per_query=max_pages_per_query,
        include_comments=include_comments,
        comment_limit_per_post=comment_limit_per_post,
        incremental=incremental,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Reddit backfill completed "
            f"corpus_id={summary.corpus_id} source_id={summary.source_id} "
            f"query_count={summary.query_count} queries_processed={summary.queries_processed} "
            f"queries_skipped={summary.queries_skipped} pages_fetched={summary.pages_fetched} "
            f"source_failures={summary.source_failures} incremental={summary.incremental}"
        )
        print(f"Corpus root: {summary.corpus_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "reddit_corpus_backfill_command_completed",
        extra={
            "event": "reddit_corpus_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_reddit_corpus_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_reddit_corpus_service(config_path, agents_config_path)
    inspection = service.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Corpus: {inspection.corpus_id}")
        print(f"Source: {inspection.source_id}")
        print(f"Root: {inspection.corpus_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed queries: {inspection.processed_query_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "reddit_corpus_inspected",
        extra={
            "event": "reddit_corpus_inspected",
            **payload,
        },
    )
    return 0


def verify_reddit_oauth_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    subreddit: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    include_comments: bool | None,
    comment_limit_per_post: int | None,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_reddit_corpus_service(config_path, agents_config_path)
    source_verification = service.verify_source(
        subreddits=subreddit,
        keywords=keyword,
        limit_per_query=limit_per_query,
        include_comments=include_comments,
        comment_limit_per_post=comment_limit_per_post,
    )
    corpus_verification = service.verify_corpus(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
    )
    payload = {
        "source_verification": source_verification.to_dict(),
        "corpus_verification": corpus_verification.to_dict(),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        source_status = "OK" if source_verification.ok else "FAIL"
        corpus_status = "OK" if corpus_verification.ok else "FAIL"
        print(f"Reddit source verification: {source_status}")
        print(f"Reddit corpus verification: {corpus_status}")
        for error in source_verification.errors:
            print(f"SOURCE ERROR: {error}")
        for warning in source_verification.warnings:
            print(f"SOURCE WARN: {warning}")
        for error in corpus_verification.errors:
            print(f"CORPUS ERROR: {error}")
        for warning in corpus_verification.warnings:
            print(f"CORPUS WARN: {warning}")
    logger.info(
        "reddit_source_verified",
        extra={
            "event": "reddit_source_verified",
            "source_ok": source_verification.ok,
            "corpus_ok": corpus_verification.ok,
            "source_id": source_verification.source_id,
            "oauth_user": source_verification.oauth_user,
            "fetched_records": source_verification.fetched_records,
            "source_error_count": len(source_verification.errors),
            "corpus_error_count": len(corpus_verification.errors),
        },
    )
    return 0 if source_verification.ok and corpus_verification.ok else 1


def backfill_x_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    account: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    max_pages_per_query: int | None,
    incremental: bool,
    as_json: bool,
) -> int:
    service = _build_x_corpus_service(config_path, agents_config_path)
    if account:
        settings = load_settings(config_path, agents_config_path)
        registry = AltDataAdapterRegistry.from_source_settings(settings.alt_data.sources)
        configured_source = registry.get(settings.strategy_research.x_corpus_default_source_id)
        configured_source.ensure_operation_supported(SourceOperation.THREAD_CONTEXT_EXPANSION)
    summary = service.backfill_x(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        accounts=account,
        keywords=keyword,
        limit_per_query=limit_per_query,
        max_pages_per_query=max_pages_per_query,
        incremental=incremental,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "X backfill completed "
            f"corpus_id={summary.corpus_id} source_id={summary.source_id} "
            f"query_count={summary.query_count} queries_processed={summary.queries_processed} "
            f"queries_skipped={summary.queries_skipped} pages_fetched={summary.pages_fetched} "
            f"source_failures={summary.source_failures} incremental={summary.incremental}"
        )
        print(f"Corpus root: {summary.corpus_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "x_corpus_backfill_command_completed",
        extra={
            "event": "x_corpus_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_x_corpus_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_x_corpus_service(config_path, agents_config_path)
    inspection = service.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Corpus: {inspection.corpus_id}")
        print(f"Source: {inspection.source_id}")
        print(f"Root: {inspection.corpus_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Processed queries: {inspection.processed_query_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "x_corpus_inspected",
        extra={
            "event": "x_corpus_inspected",
            **payload,
        },
    )
    return 0


def verify_x_source_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    account: tuple[str, ...],
    keyword: tuple[str, ...],
    limit_per_query: int | None,
    corpus_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_x_corpus_service(config_path, agents_config_path)
    if account:
        settings = load_settings(config_path, agents_config_path)
        registry = AltDataAdapterRegistry.from_source_settings(settings.alt_data.sources)
        configured_source = registry.get(settings.strategy_research.x_corpus_default_source_id)
        configured_source.ensure_operation_supported(SourceOperation.THREAD_CONTEXT_EXPANSION)
    source_verification = service.verify_source(
        accounts=account,
        keywords=keyword,
        limit_per_query=limit_per_query,
    )
    corpus_verification = service.verify_corpus(
        corpus_id=corpus_id,
        checkpoint_path=checkpoint_path,
    )
    payload = {
        "source_verification": source_verification.to_dict(),
        "corpus_verification": corpus_verification.to_dict(),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        source_status = "OK" if source_verification.ok else "FAIL"
        corpus_status = "OK" if corpus_verification.ok else "FAIL"
        print(f"X source verification: {source_status}")
        print(f"X corpus verification: {corpus_status}")
        for error in source_verification.errors:
            print(f"SOURCE ERROR: {error}")
        for warning in source_verification.warnings:
            print(f"SOURCE WARN: {warning}")
        for error in corpus_verification.errors:
            print(f"CORPUS ERROR: {error}")
        for warning in corpus_verification.warnings:
            print(f"CORPUS WARN: {warning}")
    logger.info(
        "x_source_verified",
        extra={
            "event": "x_source_verified",
            "source_ok": source_verification.ok,
            "corpus_ok": corpus_verification.ok,
            "source_id": source_verification.source_id,
            "fetched_records": source_verification.fetched_records,
            "source_error_count": len(source_verification.errors),
            "corpus_error_count": len(corpus_verification.errors),
        },
    )
    return 0 if source_verification.ok and corpus_verification.ok else 1


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


def build_labels_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    corpus_id: str | None,
    labels_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.build_labels(
        dataset_id=dataset_id,
        corpus_id=corpus_id,
        labels_path=labels_path,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Labels build completed "
            f"dataset_id={summary.dataset_id} labels_written={summary.labels_written} "
            f"skipped_missing_timestamp={summary.skipped_missing_timestamp} "
            f"skipped_unresolved_or_ambiguous={summary.skipped_unresolved_or_ambiguous}"
        )
        print(f"Labels path: {summary.labels_path}")
        print(f"Manifest: {summary.labels_manifest_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_labels_built",
        extra={
            "event": "strategy_research_labels_built",
            **payload,
        },
    )
    return 0


def run_benchmarks_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.run_benchmarks(
        dataset_id=dataset_id,
        labels_path=labels_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Benchmarks completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"split_mode={summary.split_mode} folds={summary.folds}"
        )
        print(f"Run path: {summary.run_path}")
        print("Aggregate test split:")
        test_metrics = summary.aggregate.get("test", {})
        for baseline_name, metrics in test_metrics.items():
            print(
                f"- {baseline_name}: brier={metrics.brier_score:.6f} "
                f"log_loss={metrics.log_loss:.6f} cal={metrics.calibration_error:.6f} "
                f"approval_rate={metrics.approval_rate:.4f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_benchmarks_completed",
        extra={
            "event": "strategy_research_benchmarks_completed",
            **payload,
        },
    )
    return 0


def compare_benchmarks_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_a: str | None,
    run_b: str | None,
    split: str,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    comparison = service.compare_benchmarks(
        dataset_id=dataset_id,
        run_a=run_a,
        run_b=run_b,
        split=split,
    )
    payload = comparison.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Benchmark comparison completed "
            f"dataset_id={comparison.dataset_id} run_a={comparison.run_a} run_b={comparison.run_b} split={comparison.split}"
        )
        for baseline_name, deltas in comparison.baseline_deltas.items():
            print(
                f"- {baseline_name}: "
                f"delta_brier={deltas.get('delta_brier_score', 0.0):+.6f} "
                f"delta_log_loss={deltas.get('delta_log_loss', 0.0):+.6f} "
                f"delta_cal={deltas.get('delta_calibration_error', 0.0):+.6f} "
                f"delta_approval={deltas.get('delta_approval_rate', 0.0):+.4f}"
            )
    logger.info(
        "strategy_research_benchmarks_compared",
        extra={
            "event": "strategy_research_benchmarks_compared",
            **payload,
        },
    )
    return 0


def run_ablation_study_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    alt_feature_rows_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    as_json: bool,
) -> int:
    service = _build_benchmark_service(config_path, agents_config_path)
    summary = service.run_ablation_study(
        dataset_id=dataset_id,
        labels_path=labels_path,
        alt_feature_rows_path=alt_feature_rows_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Ablation study completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"split_mode={summary.split_mode} folds={summary.folds}"
        )
        print(f"Run path: {summary.run_path}")
        print(f"Report path: {summary.report_path}")
        test_metrics = summary.aggregate.get("test", {})
        print("Aggregate test split:")
        for variant_name, metrics in test_metrics.items():
            print(
                f"- {variant_name}: "
                f"brier={float(metrics.get('brier_score', 0.0)):.6f} "
                f"log_loss={float(metrics.get('log_loss', 0.0)):.6f} "
                f"cal={float(metrics.get('calibration_error', 0.0)):.6f} "
                f"approval_rate={float(metrics.get('approval_rate', 0.0)):.4f} "
                f"approval_delta_vs_market={float(metrics.get('approval_rate_delta_vs_market_only', 0.0)):+.4f} "
                f"edge_ratio={float(metrics.get('realized_vs_expected_edge', 0.0)):.6f} "
                f"robustness={float(metrics.get('walk_forward_robustness', 0.0)):.6f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_ablation_study_completed",
        extra={
            "event": "strategy_research_ablation_study_completed",
            **payload,
        },
    )
    return 0


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


def run_walk_forward_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    labels_path: Path | None,
    baseline_name: str,
    eval_split: str,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
    min_confidence: float | None,
    min_edge_bps: int | None,
    initial_bankroll_usd: float,
    base_position_pct: float,
    max_position_pct: float,
    min_stake_usd: float,
    fee_bps: int,
    slippage_bps: int,
    as_json: bool,
) -> int:
    service = _build_walk_forward_service(config_path, agents_config_path)
    summary = service.run_walk_forward(
        dataset_id=dataset_id,
        labels_path=labels_path,
        baseline_name=baseline_name,
        eval_split=eval_split,
        train_days=train_days,
        validation_days=validation_days,
        test_days=test_days,
        step_days=step_days,
        max_folds=max_folds,
        min_confidence=min_confidence,
        min_edge_bps=min_edge_bps,
        initial_bankroll_usd=initial_bankroll_usd,
        base_position_pct=base_position_pct,
        max_position_pct=max_position_pct,
        min_stake_usd=min_stake_usd,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        metrics = summary.aggregate_metrics
        print(
            "Walk-forward strategy simulation completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} folds={len(summary.folds)} "
            f"baseline={summary.baseline_name} eval_split={summary.eval_split}"
        )
        print(f"Run path: {summary.run_path}")
        print(
            "Aggregate metrics: "
            f"roi={metrics.roi:.6f} pnl_usd={metrics.pnl_usd:.6f} "
            f"max_drawdown_pct={metrics.max_drawdown_pct:.6f} turnover={metrics.turnover:.6f} "
            f"approval_rate={metrics.approval_rate:.6f} "
            f"expected_edge_total={metrics.expected_edge_total:.6f} "
            f"realized_edge_total={metrics.realized_edge_total:.6f} "
            f"edge_capture_ratio={metrics.edge_capture_ratio:.6f}"
        )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_walk_forward_completed",
        extra={
            "event": "strategy_research_walk_forward_completed",
            **payload,
        },
    )
    return 0


def generate_strategy_report_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    output_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_walk_forward_service(config_path, agents_config_path)
    summary = service.generate_strategy_report(
        dataset_id=dataset_id,
        run_id=run_id,
        output_path=output_path,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Strategy report generated "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} report_path={summary.report_path}"
        )
    logger.info(
        "strategy_research_walk_forward_report_generated",
        extra={
            "event": "strategy_research_walk_forward_report_generated",
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


def train_baseline_models_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    feature_rows_path: Path | None,
    split_mode: str,
    train_ratio: float,
    validation_ratio: float,
    window_days: int,
    include_xgboost: bool,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.train_baseline_models(
        dataset_id=dataset_id,
        feature_rows_path=feature_rows_path,
        split_mode=split_mode,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        window_days=window_days,
        include_xgboost=include_xgboost,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Offline training completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"best_model={summary.best_model} split_mode={summary.split_mode}"
        )
        print(f"Run path: {summary.run_path}")
        for model_name, result in summary.models.items():
            test_metrics = result.metrics_by_split.get("test")
            if result.status != "trained":
                print(f"- {model_name}: status={result.status} reason={result.reason}")
                continue
            if test_metrics is None:
                print(f"- {model_name}: status=trained test_metrics=missing")
                continue
            print(
                f"- {model_name}: brier={test_metrics.brier_score:.6f} "
                f"log_loss={test_metrics.log_loss:.6f} cal={test_metrics.calibration_error:.6f} "
                f"accuracy={test_metrics.accuracy:.4f}"
            )
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_train_baseline_models_completed",
        extra={
            "event": "strategy_research_train_baseline_models_completed",
            **payload,
        },
    )
    return 0


def calibrate_model_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    model_name: str | None,
    method: str,
    fit_split: str,
    eval_split: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.calibrate_model(
        dataset_id=dataset_id,
        run_id=run_id,
        model_name=model_name,
        method=method,
        fit_split=fit_split,
        eval_split=eval_split,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Calibration completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"train_run_id={summary.train_run_id} model={summary.model_name} method={summary.method}"
        )
        print(
            "Eval split metrics: "
            f"raw_brier={summary.raw_metrics.brier_score:.6f} "
            f"calibrated_brier={summary.calibrated_metrics.brier_score:.6f} "
            f"raw_log_loss={summary.raw_metrics.log_loss:.6f} "
            f"calibrated_log_loss={summary.calibrated_metrics.log_loss:.6f}"
        )
        print(f"Calibration artifact: {summary.calibration_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "strategy_research_calibrate_model_completed",
        extra={
            "event": "strategy_research_calibrate_model_completed",
            **payload,
        },
    )
    return 0


def compare_models_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    split: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.compare_models(
        dataset_id=dataset_id,
        run_id=run_id,
        split=split,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model comparison completed "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} split={summary.split}"
        )
        for rank, (model_name, metrics) in enumerate(summary.ranking, start=1):
            deltas = summary.deltas.get(model_name, {})
            print(
                f"{rank}. {model_name}: "
                f"brier={metrics.brier_score:.6f} log_loss={metrics.log_loss:.6f} "
                f"cal={metrics.calibration_error:.6f} acc={metrics.accuracy:.4f} "
                f"delta_brier={deltas.get('delta_brier_score_vs_best', 0.0):+.6f}"
            )
    logger.info(
        "strategy_research_compare_models_completed",
        extra={
            "event": "strategy_research_compare_models_completed",
            **payload,
        },
    )
    return 0


def generate_model_card_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    run_id: str | None,
    model_name: str | None,
    calibration_run_id: str | None,
    output_path: Path | None,
    owner: str,
    decision: str,
    as_json: bool,
) -> int:
    service = _build_training_service(config_path, agents_config_path)
    summary = service.generate_model_card(
        dataset_id=dataset_id,
        run_id=run_id,
        model_name=model_name,
        calibration_run_id=calibration_run_id,
        output_path=output_path,
        owner=owner,
        decision=decision,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model card generated "
            f"dataset_id={summary.dataset_id} run_id={summary.run_id} "
            f"model={summary.model_name} decision={summary.decision}"
        )
        print(f"Output path: {summary.output_path}")
    logger.info(
        "strategy_research_model_card_generated",
        extra={
            "event": "strategy_research_model_card_generated",
            **payload,
        },
    )
    return 0
