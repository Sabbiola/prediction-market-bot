from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping


class NewsQueryKind(StrEnum):
    TOPIC = "topic"
    KEYWORD = "keyword"


@dataclass(slots=True, frozen=True)
class NewsQuery:
    value: str
    kind: NewsQueryKind = NewsQueryKind.KEYWORD

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value.strip().lower()}"


@dataclass(slots=True, frozen=True)
class NewsArticleRecord:
    source_id: str
    source_class: str
    source_name: str
    query: str
    query_kind: NewsQueryKind
    dedup_key: str
    source_record_id: str
    title: str
    summary: str
    article_url: str
    article_domain: str
    publisher: str
    published_at_utc: datetime | None
    fetched_at_utc: datetime
    source_metadata: Mapping[str, str]
    raw_payload: Mapping[str, Any]

    def to_normalized_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "query": self.query,
            "query_kind": self.query_kind.value,
            "dedup_key": self.dedup_key,
            "source_record_id": self.source_record_id,
            "title": self.title,
            "summary": self.summary,
            "article_url": self.article_url,
            "article_domain": self.article_domain,
            "publisher": self.publisher,
            "published_at_utc": self.published_at_utc.isoformat() if self.published_at_utc is not None else None,
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "source_metadata": dict(self.source_metadata),
        }

    def to_raw_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "query": self.query,
            "query_kind": self.query_kind.value,
            "dedup_key": self.dedup_key,
            "source_record_id": self.source_record_id,
            "fetched_at_utc": self.fetched_at_utc.isoformat(),
            "payload": dict(self.raw_payload),
        }


@dataclass(slots=True, frozen=True)
class NewsFetchBatch:
    source_id: str
    source_name: str
    query: str
    query_kind: NewsQueryKind
    feed_url: str
    fetched_at_utc: datetime
    retries_used: int
    cache_hit: bool
    duration_ms: float
    feed_metadata: Mapping[str, str]
    records: tuple[NewsArticleRecord, ...]

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
