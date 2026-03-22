from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


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
class EnrichmentInputRecord:
    evidence_key: str
    source_class: str
    source_name: str
    source_record_id: str
    dedup_key: str
    linkage_state: str
    linkage_confidence: float
    market_id: str
    event_id: str
    market_title: str
    event_title: str
    decision_timestamp_utc: datetime | None
    title: str
    body: str
    url: str
    published_at_utc: datetime | None
    fetched_at_utc: datetime | None

    @property
    def text(self) -> str:
        text = " ".join(part for part in (self.title.strip(), self.body.strip()) if part).strip()
        if text:
            return text
        return self.title.strip()


@dataclass(slots=True, frozen=True)
class NormalizedEnrichmentOutput:
    relevance_score: float
    extracted_claims: tuple[str, ...]
    contradiction_score: float
    contradiction_indicators: tuple[str, ...]
    novelty_score: float
    catalyst_class: str
    structured_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relevance_score": self.relevance_score,
            "extracted_claims": list(self.extracted_claims),
            "contradiction_score": self.contradiction_score,
            "contradiction_indicators": list(self.contradiction_indicators),
            "novelty_score": self.novelty_score,
            "catalyst_class": self.catalyst_class,
            "structured_summary": self.structured_summary,
        }


@dataclass(slots=True, frozen=True)
class EnrichmentNormalizationResult:
    output: NormalizedEnrichmentOutput
    violations: tuple[str, ...]


@dataclass(slots=True)
class LlmEnrichmentCheckpoint:
    enrichment_id: str
    linkage_id: str
    processed_evidence_keys: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enrichment_id": self.enrichment_id,
            "linkage_id": self.linkage_id,
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
        default_enrichment_id: str,
        default_linkage_id: str,
    ) -> "LlmEnrichmentCheckpoint":
        enrichment_id = str(payload.get("enrichment_id") or default_enrichment_id).strip() or default_enrichment_id
        linkage_id = str(payload.get("linkage_id") or default_linkage_id).strip() or default_linkage_id
        processed = payload.get("processed_evidence_keys")
        processed_keys: set[str] = set()
        if isinstance(processed, list):
            for row in processed:
                text = str(row).strip()
                if text:
                    processed_keys.add(text)
        started_at = parse_datetime_utc(payload.get("started_at_utc")) or utc_now()
        updated_at = parse_datetime_utc(payload.get("updated_at_utc")) or started_at
        completed_at = parse_datetime_utc(payload.get("completed_at_utc"))
        return cls(
            enrichment_id=enrichment_id,
            linkage_id=linkage_id,
            processed_evidence_keys=processed_keys,
            started_at_utc=started_at,
            updated_at_utc=updated_at,
            completed_at_utc=completed_at,
        )


@dataclass(slots=True, frozen=True)
class LlmEnrichmentSummary:
    enrichment_id: str
    linkage_id: str
    enrichment_root: Path
    checkpoint_path: Path
    checkpoint_completed: bool
    started_at_utc: datetime
    finished_at_utc: datetime
    records_seen: int
    records_processed: int
    records_skipped: int
    enrichments_persisted: int
    fallbacks_used: int
    failures: int
    schema_violations: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enrichment_id": self.enrichment_id,
            "linkage_id": self.linkage_id,
            "enrichment_root": str(self.enrichment_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "records_seen": self.records_seen,
            "records_processed": self.records_processed,
            "records_skipped": self.records_skipped,
            "enrichments_persisted": self.enrichments_persisted,
            "fallbacks_used": self.fallbacks_used,
            "failures": self.failures,
            "schema_violations": self.schema_violations,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class LlmEnrichmentInspection:
    enrichment_id: str
    linkage_id: str
    enrichment_root: Path
    checkpoint_path: Path
    checkpoint_present: bool
    checkpoint_completed: bool
    processed_evidence_count: int
    raw_counts: Mapping[str, int]
    normalized_counts: Mapping[str, int]
    status_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enrichment_id": self.enrichment_id,
            "linkage_id": self.linkage_id,
            "enrichment_root": str(self.enrichment_root),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_completed": self.checkpoint_completed,
            "processed_evidence_count": self.processed_evidence_count,
            "raw_counts": dict(self.raw_counts),
            "normalized_counts": dict(self.normalized_counts),
            "status_counts": dict(self.status_counts),
        }


_ALLOWED_CATALYST_CLASSES = {
    "policy",
    "regulation",
    "legal",
    "macro",
    "earnings",
    "geopolitics",
    "technology",
    "other",
}


def normalize_enrichment_output(payload: Mapping[str, Any]) -> EnrichmentNormalizationResult:
    violations: list[str] = []
    relevance_score = _normalize_probability(payload.get("relevance_score"), default=0.5, field_name="relevance_score", violations=violations)
    contradiction_score = _normalize_probability(
        payload.get("contradiction_score"),
        default=0.0,
        field_name="contradiction_score",
        violations=violations,
    )
    novelty_score = _normalize_probability(payload.get("novelty_score"), default=0.5, field_name="novelty_score", violations=violations)

    extracted_claims = _normalize_text_list(
        payload.get("extracted_claims"),
        max_items=5,
        max_len=240,
        field_name="extracted_claims",
        violations=violations,
    )
    contradiction_indicators = _normalize_text_list(
        payload.get("contradiction_indicators"),
        max_items=6,
        max_len=120,
        field_name="contradiction_indicators",
        violations=violations,
    )
    catalyst_raw = str(payload.get("catalyst_class") or "other").strip().lower()
    catalyst_class = catalyst_raw if catalyst_raw in _ALLOWED_CATALYST_CLASSES else "other"
    if catalyst_class != catalyst_raw:
        violations.append("catalyst_class_invalid")

    structured_summary = str(payload.get("structured_summary") or "").strip()
    if not structured_summary:
        structured_summary = "No structured summary available."
        violations.append("structured_summary_missing")
    if len(structured_summary) > 500:
        structured_summary = structured_summary[:500].rstrip()
        violations.append("structured_summary_truncated")

    output = NormalizedEnrichmentOutput(
        relevance_score=relevance_score,
        extracted_claims=extracted_claims,
        contradiction_score=contradiction_score,
        contradiction_indicators=contradiction_indicators,
        novelty_score=novelty_score,
        catalyst_class=catalyst_class,
        structured_summary=structured_summary,
    )
    return EnrichmentNormalizationResult(output=output, violations=tuple(violations))


def _normalize_probability(value: Any, *, default: float, field_name: str, violations: list[str]) -> float:
    if isinstance(value, bool) or value is None:
        violations.append(f"{field_name}_invalid")
        return default
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            violations.append(f"{field_name}_invalid")
            return default
        try:
            numeric = float(text)
        except ValueError:
            violations.append(f"{field_name}_invalid")
            return default
    else:
        violations.append(f"{field_name}_invalid")
        return default
    if numeric < 0.0:
        violations.append(f"{field_name}_clamped")
        return 0.0
    if numeric > 1.0:
        violations.append(f"{field_name}_clamped")
        return 1.0
    return numeric


def _normalize_text_list(
    value: Any,
    *,
    max_items: int,
    max_len: int,
    field_name: str,
    violations: list[str],
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    rows: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > max_len:
            text = text[:max_len].rstrip()
            violations.append(f"{field_name}_item_truncated")
        rows.append(text)
    if len(rows) > max_items:
        rows = rows[:max_items]
        violations.append(f"{field_name}_truncated")
    return tuple(rows)
