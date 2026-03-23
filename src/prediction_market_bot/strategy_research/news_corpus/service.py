from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import (
    GoogleNewsRssAdapter,
    NewsQuery,
    NewsQueryKind,
    NewsSourceFetchError,
)
from prediction_market_bot.infrastructure.alt_data.news_models import parse_datetime_utc

from .models import (
    NewsBackfillSummary,
    NewsCorpusInspection,
    NewsCorpusVerification,
    NewsSourceVerification,
    parse_news_query_values,
)
from .storage import NORMALIZED_FILES, RAW_FILES, NewsCorpusLayout, NewsCorpusStorage

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _Counters:
    query_count: int = 0
    queries_processed: int = 0
    queries_skipped: int = 0
    source_failures: int = 0
    retries_used: int = 0
    cache_hits: int = 0
    raw_rows_persisted: int = 0
    normalized_rows_persisted: int = 0
    normalized_rows_deduped: int = 0


class NewsCorpusService:
    def __init__(
        self,
        *,
        base_dir: Path,
        default_corpus_id: str,
        source_id: str,
        adapter: GoogleNewsRssAdapter,
        default_limit_per_query: int = 50,
        throttle_sec: float = 0.0,
    ) -> None:
        self.base_dir = base_dir
        self.default_corpus_id = default_corpus_id
        self.source_id = source_id.strip().lower()
        self.adapter = adapter
        self.default_limit_per_query = max(default_limit_per_query, 1)
        self.throttle_sec = max(throttle_sec, 0.0)

    def backfill_news(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        topics: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
    ) -> NewsBackfillSummary:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        queries = parse_news_query_values(topics=topics, keywords=keywords)
        if not queries:
            raise ValueError("At least one query is required. Use --topic and/or --keyword.")

        layout = NewsCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = NewsCorpusStorage(layout)
        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(corpus_id=effective_corpus_id, source_id=self.source_id)
        counters = _Counters(query_count=len(queries))
        warnings: list[str] = []
        started_at_utc = datetime.now(UTC)
        effective_limit = max(limit_per_query if limit_per_query is not None else self.default_limit_per_query, 1)

        existing_raw_keys = self._load_existing_raw_keys(storage)
        existing_normalized_keys = self._load_existing_normalized_keys(storage)
        query_keys = {query.key for query in queries}

        for index, query in enumerate(queries):
            if query.key in checkpoint.processed_query_keys:
                counters = _Counters(
                    query_count=counters.query_count,
                    queries_processed=counters.queries_processed,
                    queries_skipped=counters.queries_skipped + 1,
                    source_failures=counters.source_failures,
                    retries_used=counters.retries_used,
                    cache_hits=counters.cache_hits,
                    raw_rows_persisted=counters.raw_rows_persisted,
                    normalized_rows_persisted=counters.normalized_rows_persisted,
                    normalized_rows_deduped=counters.normalized_rows_deduped,
                )
                continue
            if self.throttle_sec > 0 and index > 0:
                time.sleep(self.throttle_sec)

            try:
                batch = self.adapter.fetch(query, limit=effective_limit)
            except NewsSourceFetchError as exc:
                counters = _Counters(
                    query_count=counters.query_count,
                    queries_processed=counters.queries_processed,
                    queries_skipped=counters.queries_skipped,
                    source_failures=counters.source_failures + 1,
                    retries_used=counters.retries_used,
                    cache_hits=counters.cache_hits,
                    raw_rows_persisted=counters.raw_rows_persisted,
                    normalized_rows_persisted=counters.normalized_rows_persisted,
                    normalized_rows_deduped=counters.normalized_rows_deduped,
                )
                warnings.append(f"query={query.key} fetch_failed reason={exc.reason_code} detail={exc.detail}")
                continue

            retries_used = counters.retries_used + batch.retries_used
            cache_hits = counters.cache_hits + int(batch.cache_hit)
            raw_rows_persisted = counters.raw_rows_persisted
            normalized_rows_persisted = counters.normalized_rows_persisted
            normalized_rows_deduped = counters.normalized_rows_deduped

            for record in batch.records:
                raw_key = (query.key, record.dedup_key)
                if raw_key not in existing_raw_keys:
                    existing_raw_keys.add(raw_key)
                    raw_rows_persisted += 1
                    storage.append_raw("news_payloads", record.to_raw_dict())

                if record.dedup_key in existing_normalized_keys:
                    normalized_rows_deduped += 1
                    continue
                existing_normalized_keys.add(record.dedup_key)
                normalized_rows_persisted += 1
                storage.append_normalized("news_articles", record.to_normalized_dict())

            checkpoint.processed_query_keys.add(query.key)
            checkpoint.updated_at_utc = datetime.now(UTC)
            storage.save_checkpoint(checkpoint)
            counters = _Counters(
                query_count=counters.query_count,
                queries_processed=counters.queries_processed + 1,
                queries_skipped=counters.queries_skipped,
                source_failures=counters.source_failures,
                retries_used=retries_used,
                cache_hits=cache_hits,
                raw_rows_persisted=raw_rows_persisted,
                normalized_rows_persisted=normalized_rows_persisted,
                normalized_rows_deduped=normalized_rows_deduped,
            )

        if query_keys.issubset(checkpoint.processed_query_keys):
            checkpoint.completed_at_utc = datetime.now(UTC)
        else:
            checkpoint.completed_at_utc = None
        checkpoint.updated_at_utc = datetime.now(UTC)
        storage.save_checkpoint(checkpoint)

        finished_at_utc = datetime.now(UTC)
        manifest = {
            "corpus_id": effective_corpus_id,
            "source_id": self.source_id,
            "updated_at_utc": finished_at_utc.isoformat(),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "processed_query_count": len(checkpoint.processed_query_keys),
        }
        storage.write_manifest(manifest)
        logger.info(
            "news_corpus_backfill_completed",
            extra={
                "event": "news_corpus_backfill_completed",
                "corpus_id": effective_corpus_id,
                "source_id": self.source_id,
                "query_count": counters.query_count,
                "queries_processed": counters.queries_processed,
                "queries_skipped": counters.queries_skipped,
                "source_failures": counters.source_failures,
                "retries_used": counters.retries_used,
                "cache_hits": counters.cache_hits,
                "raw_rows_persisted": counters.raw_rows_persisted,
                "normalized_rows_persisted": counters.normalized_rows_persisted,
                "normalized_rows_deduped": counters.normalized_rows_deduped,
            },
        )
        return NewsBackfillSummary(
            corpus_id=effective_corpus_id,
            source_id=self.source_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            query_count=counters.query_count,
            queries_processed=counters.queries_processed,
            queries_skipped=counters.queries_skipped,
            source_failures=counters.source_failures,
            retries_used=counters.retries_used,
            cache_hits=counters.cache_hits,
            raw_rows_persisted=counters.raw_rows_persisted,
            normalized_rows_persisted=counters.normalized_rows_persisted,
            normalized_rows_deduped=counters.normalized_rows_deduped,
            warnings=tuple(warnings),
        )

    def inspect_corpus(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> NewsCorpusInspection:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        layout = NewsCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = NewsCorpusStorage(layout)
        checkpoint = storage.load_checkpoint(corpus_id=effective_corpus_id, source_id=self.source_id)
        return NewsCorpusInspection(
            corpus_id=effective_corpus_id,
            source_id=self.source_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            processed_query_count=len(checkpoint.processed_query_keys),
            raw_counts={name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            normalized_counts={name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
        )

    def verify_corpus(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> NewsCorpusVerification:
        inspection = self.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
        layout = NewsCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=inspection.corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = NewsCorpusStorage(layout)
        normalized_rows = storage.read_rows(normalized=True, name="news_articles")

        errors: list[str] = []
        warnings: list[str] = []
        if not normalized_rows:
            warnings.append("normalized/news_articles.jsonl is empty")

        seen_dedup_keys: set[str] = set()
        duplicates = 0
        for row in normalized_rows:
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_id = str(row.get("source_id") or "").strip()
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            article_url = str(row.get("article_url") or "").strip()
            title = str(row.get("title") or "").strip()
            fetched_at = str(row.get("fetched_at_utc") or "").strip()
            if not dedup_key:
                errors.append("missing dedup_key")
            if not source_id:
                errors.append("missing source_id")
            if not query:
                errors.append("missing query")
            if query_kind not in {"topic", "keyword"}:
                errors.append(f"invalid query_kind={query_kind or 'missing'}")
            if not article_url:
                errors.append("missing article_url")
            if not title:
                errors.append("missing title")
            if parse_datetime_utc(fetched_at) is None:
                errors.append("missing_or_invalid fetched_at_utc")
            published_at_raw = row.get("published_at_utc")
            if published_at_raw is not None and parse_datetime_utc(published_at_raw) is None:
                errors.append("invalid published_at_utc")
            if dedup_key:
                if dedup_key in seen_dedup_keys:
                    duplicates += 1
                else:
                    seen_dedup_keys.add(dedup_key)
        if duplicates > 0:
            errors.append(f"duplicate normalized dedup_key rows={duplicates}")

        return NewsCorpusVerification(
            corpus_id=inspection.corpus_id,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details={
                "source_id": self.source_id,
                "corpus_root": str(inspection.corpus_root),
                "checkpoint_path": str(inspection.checkpoint_path),
                "checkpoint_completed": inspection.checkpoint_completed,
                "raw_counts": dict(inspection.raw_counts),
                "normalized_counts": dict(inspection.normalized_counts),
                "duplicate_dedup_keys": duplicates,
            },
        )

    def verify_source(
        self,
        *,
        topics: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
    ) -> NewsSourceVerification:
        queries = parse_news_query_values(topics=topics, keywords=keywords)
        if not queries:
            queries = (NewsQuery(value="WORLD", kind=NewsQueryKind.TOPIC),)
        effective_limit = max(limit_per_query if limit_per_query is not None else self.default_limit_per_query, 1)
        fetched_articles = 0
        errors: list[str] = []
        details: dict[str, object] = {}
        for query in queries:
            try:
                batch = self.adapter.fetch(query, limit=effective_limit)
            except NewsSourceFetchError as exc:
                errors.append(f"query={query.key} fetch_failed reason={exc.reason_code} detail={exc.detail}")
                continue
            fetched_articles += len(batch.records)
            details[query.key] = {
                "feed_url": batch.feed_url,
                "cache_hit": batch.cache_hit,
                "retries_used": batch.retries_used,
                "records": len(batch.records),
            }
        return NewsSourceVerification(
            source_id=self.source_id,
            ok=not errors,
            checked_queries=queries,
            fetched_articles=fetched_articles,
            errors=tuple(errors),
            warnings=(),
            details=details,
        )

    @staticmethod
    def _load_existing_raw_keys(storage: NewsCorpusStorage) -> set[tuple[str, str]]:
        rows: set[tuple[str, str]] = set()
        for row in storage.read_rows(normalized=False, name="news_payloads"):
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            dedup_key = str(row.get("dedup_key") or "").strip()
            if not query or not query_kind or not dedup_key:
                continue
            rows.add((f"{query_kind}:{query.lower()}", dedup_key))
        return rows

    @staticmethod
    def _load_existing_normalized_keys(storage: NewsCorpusStorage) -> set[str]:
        rows: set[str] = set()
        for row in storage.read_rows(normalized=True, name="news_articles"):
            dedup_key = str(row.get("dedup_key") or "").strip()
            if dedup_key:
                rows.add(dedup_key)
        return rows
