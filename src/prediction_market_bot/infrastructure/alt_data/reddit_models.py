from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping


class RedditQueryKind(StrEnum):
    SUBREDDIT = "subreddit"
    KEYWORD = "keyword"


class RedditEvidenceKind(StrEnum):
    SUBMISSION = "submission"
    COMMENT = "comment"


@dataclass(slots=True, frozen=True)
class RedditQuery:
    value: str
    kind: RedditQueryKind = RedditQueryKind.KEYWORD

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value.strip().lower()}"


@dataclass(slots=True, frozen=True)
class RedditEvidenceRecord:
    source_id: str
    source_class: str
    source_name: str
    query: str
    query_kind: RedditQueryKind
    evidence_kind: RedditEvidenceKind
    dedup_key: str
    source_record_id: str
    post_id: str
    comment_id: str
    parent_post_id: str
    subreddit: str
    subreddit_id: str
    title: str
    body: str
    author: str
    score: int
    num_comments: int | None
    permalink_url: str
    external_url: str
    created_at_utc: datetime | None
    fetched_at_utc: datetime
    subreddit_metadata: Mapping[str, Any]
    source_metadata: Mapping[str, Any]
    raw_payload: Mapping[str, Any]

    def to_normalized_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "query": self.query,
            "query_kind": self.query_kind.value,
            "evidence_kind": self.evidence_kind.value,
            "dedup_key": self.dedup_key,
            "source_record_id": self.source_record_id,
            "post_id": self.post_id,
            "comment_id": self.comment_id,
            "parent_post_id": self.parent_post_id,
            "subreddit": self.subreddit,
            "subreddit_id": self.subreddit_id,
            "title": self.title,
            "body": self.body,
            "author": self.author,
            "score": self.score,
            "num_comments": self.num_comments,
            "permalink_url": self.permalink_url,
            "external_url": self.external_url,
            "created_at_utc": self.created_at_utc.isoformat() if self.created_at_utc is not None else None,
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "subreddit_metadata": dict(self.subreddit_metadata),
            "source_metadata": dict(self.source_metadata),
        }

    def to_raw_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "query": self.query,
            "query_kind": self.query_kind.value,
            "evidence_kind": self.evidence_kind.value,
            "dedup_key": self.dedup_key,
            "source_record_id": self.source_record_id,
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "payload": dict(self.raw_payload),
        }


@dataclass(slots=True, frozen=True)
class RedditFetchPage:
    source_id: str
    source_name: str
    query: str
    query_kind: RedditQueryKind
    fetched_at_utc: datetime
    retries_used: int
    duration_ms: float
    cache_hit: bool
    next_cursor: str | None
    rate_limit_remaining: float | None
    rate_limit_reset_sec: float | None
    rate_limit_used: float | None
    records: tuple[RedditEvidenceRecord, ...]

    @property
    def raw_count(self) -> int:
        return len(self.records)

    @property
    def normalized_count(self) -> int:
        return len(self.records)


def parse_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_epoch_utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromtimestamp(float(text), tz=UTC)
        except (ValueError, OSError):
            return parse_datetime_utc(text)
    return None


def build_reddit_dedup_key(
    *,
    evidence_kind: RedditEvidenceKind,
    source_record_id: str,
    permalink_url: str,
    created_at_utc: datetime | None,
    query: RedditQuery,
) -> str:
    canonical = "|".join(
        [
            evidence_kind.value,
            source_record_id.strip().lower(),
            permalink_url.strip().lower(),
            created_at_utc.isoformat() if created_at_utc is not None else "",
            query.kind.value,
            query.value.strip().lower(),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"reddit:sha256:{digest}"
