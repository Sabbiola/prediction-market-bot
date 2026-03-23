from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.infrastructure.alt_data import NewsQuery, NewsQueryKind
from prediction_market_bot.infrastructure.alt_data.news_models import parse_datetime_utc


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class NewsCorpusCheckpoint:
    corpus_id: str
    source_id: str
    processed_query_keys: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_id": self.source_id,
            "processed_query_keys": sorted(self.processed_query_keys),
            "started_at_utc": self.started_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "completed_at_utc": self.completed_at_utc.isoformat() if self.completed_at_utc is not None else None,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        default_corpus_id: str,
        default_source_id: str,
    ) -> "NewsCorpusCheckpoint":
        corpus_id = str(payload.get("corpus_id") or default_corpus_id).strip() or default_corpus_id
        source_id = str(payload.get("source_id") or default_source_id).strip() or default_source_id
        processed_query_keys: set[str] = set()
        raw_processed = payload.get("processed_query_keys")
        if isinstance(raw_processed, list):
            for row in raw_processed:
                if isinstance(row, str) and row.strip():
                    processed_query_keys.add(row.strip())
        started_at_utc = parse_datetime_utc(payload.get("started_at_utc")) or utc_now()
        updated_at_utc = parse_datetime_utc(payload.get("updated_at_utc")) or started_at_utc
        completed_at_utc = parse_datetime_utc(payload.get("completed_at_utc"))
        return cls(
            corpus_id=corpus_id,
            source_id=source_id,
            processed_query_keys=processed_query_keys,
            started_at_utc=started_at_utc,
            updated_at_utc=updated_at_utc,
            completed_at_utc=completed_at_utc,
        )


@dataclass(slots=True, frozen=True)
class NewsBackfillSummary:
    corpus_id: str
    source_id: str
    corpus_root: Path
    checkpoint_path: Path
    checkpoint_completed: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    query_count: int
    queries_processed: int
    queries_skipped: int
    source_failures: int
    retries_used: int
    cache_hits: int
    raw_rows_persisted: int
    normalized_rows_persisted: int
    normalized_rows_deduped: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_id": self.source_id,
            "corpus_root": str(self.corpus_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "query_count": self.query_count,
            "queries_processed": self.queries_processed,
            "queries_skipped": self.queries_skipped,
            "source_failures": self.source_failures,
            "retries_used": self.retries_used,
            "cache_hits": self.cache_hits,
            "raw_rows_persisted": self.raw_rows_persisted,
            "normalized_rows_persisted": self.normalized_rows_persisted,
            "normalized_rows_deduped": self.normalized_rows_deduped,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class NewsCorpusInspection:
    corpus_id: str
    source_id: str
    corpus_root: Path
    checkpoint_path: Path
    checkpoint_present: bool
    checkpoint_completed: bool
    processed_query_count: int
    raw_counts: Mapping[str, int]
    normalized_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_id": self.source_id,
            "corpus_root": str(self.corpus_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_completed": self.checkpoint_completed,
            "processed_query_count": self.processed_query_count,
            "raw_counts": dict(self.raw_counts),
            "normalized_counts": dict(self.normalized_counts),
        }


@dataclass(slots=True, frozen=True)
class NewsCorpusVerification:
    corpus_id: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


@dataclass(slots=True, frozen=True)
class NewsSourceVerification:
    source_id: str
    ok: bool
    checked_queries: tuple[NewsQuery, ...]
    fetched_articles: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "ok": self.ok,
            "checked_queries": [
                {
                    "query": row.value,
                    "query_kind": row.kind.value,
                }
                for row in self.checked_queries
            ],
            "fetched_articles": self.fetched_articles,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


def parse_news_query_values(*, topics: tuple[str, ...], keywords: tuple[str, ...]) -> tuple[NewsQuery, ...]:
    normalized_topics = [value.strip() for value in topics if value.strip()]
    normalized_keywords = [value.strip() for value in keywords if value.strip()]
    rows: list[NewsQuery] = []
    for topic in normalized_topics:
        rows.append(NewsQuery(value=topic, kind=NewsQueryKind.TOPIC))
    for keyword in normalized_keywords:
        rows.append(NewsQuery(value=keyword, kind=NewsQueryKind.KEYWORD))
    return tuple(rows)
