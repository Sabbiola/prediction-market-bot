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
from prediction_market_bot.strategy_research.news_corpus import NewsCorpusService
from prediction_market_bot.strategy_research.reddit_corpus import RedditCorpusService
from prediction_market_bot.strategy_research.research_corpus import ResearchEvidenceArchivalService
from prediction_market_bot.strategy_research.x_corpus import XCorpusService

logger = logging.getLogger(__name__)


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
