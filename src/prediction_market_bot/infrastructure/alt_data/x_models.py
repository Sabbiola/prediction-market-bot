from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping


class XQueryKind(StrEnum):
    KEYWORD = "keyword"
    ACCOUNT = "account"


@dataclass(slots=True, frozen=True)
class XQuery:
    value: str
    kind: XQueryKind = XQueryKind.KEYWORD

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.value.strip().lower()}"


@dataclass(slots=True, frozen=True)
class XPostRecord:
    source_id: str
    source_class: str
    source_name: str
    query: str
    query_kind: XQueryKind
    dedup_key: str
    source_record_id: str
    post_id: str
    post_url: str
    author_id: str
    author_username: str
    text: str
    lang: str
    conversation_id: str
    public_metrics: Mapping[str, int]
    created_at_utc: datetime | None
    fetched_at_utc: datetime
    source_metadata: Mapping[str, Any]
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
            "post_id": self.post_id,
            "post_url": self.post_url,
            "author_id": self.author_id,
            "author_username": self.author_username,
            "text": self.text,
            "lang": self.lang,
            "conversation_id": self.conversation_id,
            "public_metrics": dict(self.public_metrics),
            "created_at_utc": self.created_at_utc.isoformat() if self.created_at_utc is not None else None,
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
class XFetchPage:
    source_id: str
    source_name: str
    query: str
    query_kind: XQueryKind
    fetched_at_utc: datetime
    retries_used: int
    duration_ms: float
    cache_hit: bool
    next_cursor: str | None
    rate_limit_remaining: float | None
    rate_limit_reset_epoch: float | None
    records: tuple[XPostRecord, ...]

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


def build_x_dedup_key(*, source_record_id: str, created_at_utc: datetime | None, query: XQuery) -> str:
    canonical = "|".join(
        [
            source_record_id.strip().lower(),
            created_at_utc.isoformat() if created_at_utc is not None else "",
            query.kind.value,
            query.value.strip().lower(),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"x:sha256:{digest}"
