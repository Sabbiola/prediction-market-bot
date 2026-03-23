from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding


@dataclass(slots=True, frozen=True)
class SourceFetchBatch:
    source_name: str
    source_type: SourceType
    query: str
    raw_count: int
    normalized_count: int
    retries_used: int
    cache_hit: bool
    duration_ms: float
    findings: tuple[ResearchFinding, ...]
    raw_records: tuple[dict[str, Any], ...]


@dataclass(slots=True, frozen=True)
class ResearchIngestionBatch:
    run_id: str
    market_id: str
    source_count: int
    source_failures: int
    raw_count: int
    normalized_count: int
    deduplicated_count: int
    retries_used: int
    cache_hits: int
    source_total_duration_ms: float
    ingestion_duration_ms: float
    findings: tuple[ResearchFinding, ...]


class ResearchSourcePayloadError(RuntimeError):
    def __init__(self, *, source_name: str, reason_code: str, message: str) -> None:
        super().__init__(f"{source_name}: {reason_code} ({message})")
        self.source_name = source_name
        self.reason_code = reason_code
        self.message = message


@dataclass(slots=True, frozen=True)
class ResearchNormalizationContext:
    market: MarketSnapshot
    query: str
    fetched_at: datetime
    source_name: str
    source_type: SourceType
    endpoint_url: str


def utc_now() -> datetime:
    return datetime.now(UTC)
