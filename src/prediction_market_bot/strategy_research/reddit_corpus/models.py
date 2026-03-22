from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.infrastructure.alt_data import RedditQuery, RedditQueryKind
from prediction_market_bot.infrastructure.alt_data.reddit_models import parse_datetime_utc


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RedditCorpusCheckpoint:
    corpus_id: str
    source_id: str
    query_cursors: dict[str, str] = field(default_factory=dict)
    completed_query_keys: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None
    last_incremental_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_id": self.source_id,
            "query_cursors": dict(sorted(self.query_cursors.items())),
            "completed_query_keys": sorted(self.completed_query_keys),
            "started_at_utc": self.started_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "completed_at_utc": self.completed_at_utc.isoformat() if self.completed_at_utc is not None else None,
            "last_incremental_at_utc": (
                self.last_incremental_at_utc.isoformat() if self.last_incremental_at_utc is not None else None
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        default_corpus_id: str,
        default_source_id: str,
    ) -> "RedditCorpusCheckpoint":
        corpus_id = str(payload.get("corpus_id") or default_corpus_id).strip() or default_corpus_id
        source_id = str(payload.get("source_id") or default_source_id).strip() or default_source_id
        query_cursors: dict[str, str] = {}
        raw_cursors = payload.get("query_cursors")
        if isinstance(raw_cursors, Mapping):
            for key, value in raw_cursors.items():
                key_text = str(key).strip()
                value_text = str(value).strip()
                if key_text and value_text:
                    query_cursors[key_text] = value_text
        completed_query_keys: set[str] = set()
        raw_completed = payload.get("completed_query_keys")
        if isinstance(raw_completed, list):
            for row in raw_completed:
                if isinstance(row, str) and row.strip():
                    completed_query_keys.add(row.strip())
        started_at_utc = parse_datetime_utc(payload.get("started_at_utc")) or utc_now()
        updated_at_utc = parse_datetime_utc(payload.get("updated_at_utc")) or started_at_utc
        completed_at_utc = parse_datetime_utc(payload.get("completed_at_utc"))
        last_incremental_at_utc = parse_datetime_utc(payload.get("last_incremental_at_utc"))
        return cls(
            corpus_id=corpus_id,
            source_id=source_id,
            query_cursors=query_cursors,
            completed_query_keys=completed_query_keys,
            started_at_utc=started_at_utc,
            updated_at_utc=updated_at_utc,
            completed_at_utc=completed_at_utc,
            last_incremental_at_utc=last_incremental_at_utc,
        )


@dataclass(slots=True, frozen=True)
class RedditBackfillSummary:
    corpus_id: str
    source_id: str
    corpus_root: Path
    checkpoint_path: Path
    checkpoint_completed: bool
    incremental: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    query_count: int
    queries_processed: int
    queries_skipped: int
    pages_fetched: int
    source_failures: int
    retries_used: int
    raw_rows_persisted: int
    normalized_rows_persisted: int
    normalized_rows_deduped: int
    submissions_persisted: int
    comments_persisted: int
    subreddit_rows_persisted: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_id": self.source_id,
            "corpus_root": str(self.corpus_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "incremental": self.incremental,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "query_count": self.query_count,
            "queries_processed": self.queries_processed,
            "queries_skipped": self.queries_skipped,
            "pages_fetched": self.pages_fetched,
            "source_failures": self.source_failures,
            "retries_used": self.retries_used,
            "raw_rows_persisted": self.raw_rows_persisted,
            "normalized_rows_persisted": self.normalized_rows_persisted,
            "normalized_rows_deduped": self.normalized_rows_deduped,
            "submissions_persisted": self.submissions_persisted,
            "comments_persisted": self.comments_persisted,
            "subreddit_rows_persisted": self.subreddit_rows_persisted,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class RedditCorpusInspection:
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
class RedditCorpusVerification:
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
class RedditSourceVerification:
    source_id: str
    ok: bool
    checked_queries: tuple[RedditQuery, ...]
    oauth_user: str
    fetched_records: int
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
            "oauth_user": self.oauth_user,
            "fetched_records": self.fetched_records,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


def parse_reddit_query_values(*, subreddits: tuple[str, ...], keywords: tuple[str, ...]) -> tuple[RedditQuery, ...]:
    normalized_subreddits = [value.strip().lstrip("r/") for value in subreddits if value.strip()]
    normalized_keywords = [value.strip() for value in keywords if value.strip()]
    rows: list[RedditQuery] = []
    for subreddit in normalized_subreddits:
        if subreddit:
            rows.append(RedditQuery(value=subreddit, kind=RedditQueryKind.SUBREDDIT))
    for keyword in normalized_keywords:
        rows.append(RedditQuery(value=keyword, kind=RedditQueryKind.KEYWORD))
    return tuple(rows)
