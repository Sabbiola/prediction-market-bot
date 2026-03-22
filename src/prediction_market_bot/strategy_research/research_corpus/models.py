from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(UTC)


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
        if len(text) == 10 and text.count("-") == 2:
            try:
                parsed = datetime.fromisoformat(f"{text}T00:00:00+00:00")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True, frozen=True)
class MarketDecisionPoint:
    market_id: str
    event_id: str
    market_title: str
    market_category: str
    decision_timestamp_utc: datetime
    resolved_outcome: str
    resolved_at_utc: datetime | None
    yes_price_last: float | None
    liquidity_usd: float | None
    volume_24h_usd: float | None

    @property
    def decision_date(self) -> date:
        return self.decision_timestamp_utc.date()

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "event_id": self.event_id,
            "market_title": self.market_title,
            "market_category": self.market_category,
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "resolved_outcome": self.resolved_outcome,
            "resolved_at_utc": self.resolved_at_utc.isoformat() if self.resolved_at_utc is not None else None,
            "yes_price_last": self.yes_price_last,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h_usd": self.volume_24h_usd,
        }


@dataclass(slots=True)
class ResearchCorpusCheckpoint:
    corpus_id: str
    source_dataset_id: str
    processed_market_ids: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_dataset_id": self.source_dataset_id,
            "processed_market_ids": sorted(self.processed_market_ids),
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
        default_source_dataset_id: str,
    ) -> "ResearchCorpusCheckpoint":
        corpus_id = str(payload.get("corpus_id") or default_corpus_id).strip() or default_corpus_id
        source_dataset_id = (
            str(payload.get("source_dataset_id") or default_source_dataset_id).strip() or default_source_dataset_id
        )
        processed: set[str] = set()
        raw_processed = payload.get("processed_market_ids")
        if isinstance(raw_processed, list):
            for row in raw_processed:
                if isinstance(row, str) and row.strip():
                    processed.add(row.strip())
        started = parse_datetime_utc(payload.get("started_at_utc")) or utc_now()
        updated = parse_datetime_utc(payload.get("updated_at_utc")) or started
        completed = parse_datetime_utc(payload.get("completed_at_utc"))
        return cls(
            corpus_id=corpus_id,
            source_dataset_id=source_dataset_id,
            processed_market_ids=processed,
            started_at_utc=started,
            updated_at_utc=updated,
            completed_at_utc=completed,
        )


@dataclass(slots=True, frozen=True)
class ResearchCorpusBackfillSummary:
    corpus_id: str
    source_dataset_id: str
    corpus_root: Path
    checkpoint_path: Path
    checkpoint_completed: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    decision_points_seen: int
    decision_points_processed: int
    decision_points_skipped: int
    source_batches: int
    raw_payloads_persisted: int
    normalized_findings_persisted: int
    normalized_findings_deduped: int
    findings_filtered_missing_published_at: int
    findings_filtered_post_decision: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_dataset_id": self.source_dataset_id,
            "corpus_root": str(self.corpus_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "decision_points_seen": self.decision_points_seen,
            "decision_points_processed": self.decision_points_processed,
            "decision_points_skipped": self.decision_points_skipped,
            "source_batches": self.source_batches,
            "raw_payloads_persisted": self.raw_payloads_persisted,
            "normalized_findings_persisted": self.normalized_findings_persisted,
            "normalized_findings_deduped": self.normalized_findings_deduped,
            "findings_filtered_missing_published_at": self.findings_filtered_missing_published_at,
            "findings_filtered_post_decision": self.findings_filtered_post_decision,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class ResearchCorpusInspection:
    corpus_id: str
    source_dataset_id: str
    corpus_root: Path
    checkpoint_path: Path
    checkpoint_present: bool
    checkpoint_completed: bool
    processed_market_count: int
    raw_counts: Mapping[str, int]
    normalized_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "source_dataset_id": self.source_dataset_id,
            "corpus_root": str(self.corpus_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_completed": self.checkpoint_completed,
            "processed_market_count": self.processed_market_count,
            "raw_counts": dict(self.raw_counts),
            "normalized_counts": dict(self.normalized_counts),
        }


@dataclass(slots=True, frozen=True)
class ResearchAlignmentVerification:
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
