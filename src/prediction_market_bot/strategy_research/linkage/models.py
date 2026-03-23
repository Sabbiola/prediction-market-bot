from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
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


class LinkageState(StrEnum):
    LINKED = "linked"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    STALE_EVIDENCE = "stale_evidence"


@dataclass(slots=True, frozen=True)
class MarketReference:
    market_id: str
    event_id: str
    market_title: str
    event_title: str
    category: str
    decision_timestamp_utc: datetime

    @property
    def decision_date(self) -> date:
        return self.decision_timestamp_utc.date()


@dataclass(slots=True, frozen=True)
class EvidenceRecord:
    evidence_key: str
    source_class: str
    source_name: str
    source_record_id: str
    dedup_key: str
    title: str
    body: str
    url: str
    published_at_utc: datetime | None
    fetched_at_utc: datetime | None
    metadata: Mapping[str, Any]

    @property
    def searchable_text(self) -> str:
        return " ".join(part for part in (self.title.strip(), self.body.strip()) if part).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "source_record_id": self.source_record_id,
            "dedup_key": self.dedup_key,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "published_at_utc": self.published_at_utc.isoformat() if self.published_at_utc is not None else None,
            "fetched_at_utc": self.fetched_at_utc.isoformat() if self.fetched_at_utc is not None else None,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class LinkageCandidateScore:
    market_id: str
    event_id: str
    market_title: str
    event_title: str
    decision_timestamp_utc: datetime
    title_similarity: float
    event_similarity: float
    token_overlap: float
    alias_overlap: float
    recency_score: float
    final_score: float
    time_valid: bool
    time_delta_hours: float | None
    reason_codes: tuple[str, ...]
    matched_aliases: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "event_id": self.event_id,
            "market_title": self.market_title,
            "event_title": self.event_title,
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "title_similarity": self.title_similarity,
            "event_similarity": self.event_similarity,
            "token_overlap": self.token_overlap,
            "alias_overlap": self.alias_overlap,
            "recency_score": self.recency_score,
            "final_score": self.final_score,
            "time_valid": self.time_valid,
            "time_delta_hours": self.time_delta_hours,
            "reason_codes": list(self.reason_codes),
            "matched_aliases": list(self.matched_aliases),
        }


@dataclass(slots=True, frozen=True)
class LinkageResult:
    linkage_id: str
    dataset_id: str
    source_class: str
    source_name: str
    evidence_key: str
    source_record_id: str
    dedup_key: str
    state: LinkageState
    confidence: float
    ambiguity_score: float
    matched_market_id: str
    matched_event_id: str
    matched_market_title: str
    matched_event_title: str
    decision_timestamp_utc: datetime | None
    evidence_title: str
    evidence_url: str
    published_at_utc: datetime | None
    fetched_at_utc: datetime | None
    extracted_entities: tuple[str, ...]
    matched_aliases: tuple[str, ...]
    top_candidates: tuple[LinkageCandidateScore, ...]
    rationale: tuple[str, ...]
    created_at_utc: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkage_id": self.linkage_id,
            "dataset_id": self.dataset_id,
            "source_class": self.source_class,
            "source_name": self.source_name,
            "evidence_key": self.evidence_key,
            "source_record_id": self.source_record_id,
            "dedup_key": self.dedup_key,
            "state": self.state.value,
            "confidence": self.confidence,
            "ambiguity_score": self.ambiguity_score,
            "matched_market_id": self.matched_market_id,
            "matched_event_id": self.matched_event_id,
            "matched_market_title": self.matched_market_title,
            "matched_event_title": self.matched_event_title,
            "decision_timestamp_utc": (
                self.decision_timestamp_utc.isoformat() if self.decision_timestamp_utc is not None else None
            ),
            "evidence_title": self.evidence_title,
            "evidence_url": self.evidence_url,
            "published_at_utc": self.published_at_utc.isoformat() if self.published_at_utc is not None else None,
            "fetched_at_utc": self.fetched_at_utc.isoformat() if self.fetched_at_utc is not None else None,
            "extracted_entities": list(self.extracted_entities),
            "matched_aliases": list(self.matched_aliases),
            "top_candidates": [row.to_dict() for row in self.top_candidates],
            "rationale": list(self.rationale),
            "created_at_utc": self.created_at_utc.isoformat(),
        }


@dataclass(slots=True)
class LinkageCheckpoint:
    linkage_id: str
    dataset_id: str
    processed_evidence_keys: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkage_id": self.linkage_id,
            "dataset_id": self.dataset_id,
            "processed_evidence_keys": sorted(self.processed_evidence_keys),
            "started_at_utc": self.started_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "completed_at_utc": self.completed_at_utc.isoformat() if self.completed_at_utc is not None else None,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        default_linkage_id: str,
        default_dataset_id: str,
    ) -> "LinkageCheckpoint":
        linkage_id = str(payload.get("linkage_id") or default_linkage_id).strip() or default_linkage_id
        dataset_id = str(payload.get("dataset_id") or default_dataset_id).strip() or default_dataset_id
        processed_keys: set[str] = set()
        raw_processed = payload.get("processed_evidence_keys")
        if isinstance(raw_processed, list):
            for row in raw_processed:
                text = str(row).strip()
                if text:
                    processed_keys.add(text)
        started = parse_datetime_utc(payload.get("started_at_utc")) or utc_now()
        updated = parse_datetime_utc(payload.get("updated_at_utc")) or started
        completed = parse_datetime_utc(payload.get("completed_at_utc"))
        return cls(
            linkage_id=linkage_id,
            dataset_id=dataset_id,
            processed_evidence_keys=processed_keys,
            started_at_utc=started,
            updated_at_utc=updated,
            completed_at_utc=completed,
        )


@dataclass(slots=True, frozen=True)
class LinkageBuildSummary:
    linkage_id: str
    dataset_id: str
    linkage_root: Path
    checkpoint_path: Path
    checkpoint_completed: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    evidence_seen: int
    evidence_processed: int
    evidence_skipped: int
    candidate_rows_persisted: int
    candidate_rows_deduped: int
    linked_count: int
    ambiguous_count: int
    unresolved_count: int
    stale_count: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkage_id": self.linkage_id,
            "dataset_id": self.dataset_id,
            "linkage_root": str(self.linkage_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "evidence_seen": self.evidence_seen,
            "evidence_processed": self.evidence_processed,
            "evidence_skipped": self.evidence_skipped,
            "candidate_rows_persisted": self.candidate_rows_persisted,
            "candidate_rows_deduped": self.candidate_rows_deduped,
            "linked_count": self.linked_count,
            "ambiguous_count": self.ambiguous_count,
            "unresolved_count": self.unresolved_count,
            "stale_count": self.stale_count,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class LinkageInspection:
    linkage_id: str
    dataset_id: str
    linkage_root: Path
    checkpoint_path: Path
    checkpoint_present: bool
    checkpoint_completed: bool
    processed_evidence_count: int
    raw_counts: Mapping[str, int]
    normalized_counts: Mapping[str, int]
    state_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkage_id": self.linkage_id,
            "dataset_id": self.dataset_id,
            "linkage_root": str(self.linkage_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_completed": self.checkpoint_completed,
            "processed_evidence_count": self.processed_evidence_count,
            "raw_counts": dict(self.raw_counts),
            "normalized_counts": dict(self.normalized_counts),
            "state_counts": dict(self.state_counts),
        }


@dataclass(slots=True, frozen=True)
class LinkageQualityVerification:
    linkage_id: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "linkage_id": self.linkage_id,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }
