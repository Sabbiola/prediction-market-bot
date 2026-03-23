from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .models import (
    EnrichmentInputRecord,
    LlmEnrichmentInspection,
    LlmEnrichmentSummary,
    normalize_enrichment_output,
    parse_datetime_utc,
)
from .provider import LlmEnrichmentProvider
from .storage import LlmEnrichmentLayout, LlmEnrichmentStorage, NORMALIZED_FILES, RAW_FILES

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _Counters:
    records_processed: int = 0
    records_skipped: int = 0
    enrichments_persisted: int = 0
    fallbacks_used: int = 0
    failures: int = 0
    schema_violations: int = 0


class AltDataLlmEnrichmentService:
    def __init__(
        self,
        *,
        base_dir: Path,
        linkage_base_dir: Path,
        news_corpus_base_dir: Path,
        reddit_corpus_base_dir: Path,
        x_corpus_base_dir: Path,
        default_enrichment_id: str,
        default_linkage_id: str,
        default_news_corpus_id: str,
        default_reddit_corpus_id: str,
        default_x_corpus_id: str,
        enabled: bool,
        prompt_version: str,
        include_states: tuple[str, ...],
        throttle_sec: float,
        primary_provider: LlmEnrichmentProvider,
        fallback_provider: LlmEnrichmentProvider | None,
    ) -> None:
        self.base_dir = base_dir
        self.linkage_base_dir = linkage_base_dir
        self.news_corpus_base_dir = news_corpus_base_dir
        self.reddit_corpus_base_dir = reddit_corpus_base_dir
        self.x_corpus_base_dir = x_corpus_base_dir
        self.default_enrichment_id = default_enrichment_id
        self.default_linkage_id = default_linkage_id
        self.default_news_corpus_id = default_news_corpus_id
        self.default_reddit_corpus_id = default_reddit_corpus_id
        self.default_x_corpus_id = default_x_corpus_id
        self.enabled = enabled
        self.prompt_version = prompt_version.strip() or "v1"
        self.include_states = tuple(state.strip().lower() for state in include_states if state.strip())
        self.throttle_sec = max(throttle_sec, 0.0)
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider

    def enrich_alt_data(
        self,
        *,
        enrichment_id: str | None = None,
        linkage_id: str | None = None,
        news_corpus_id: str | None = None,
        reddit_corpus_id: str | None = None,
        x_corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        limit_records: int | None = None,
    ) -> LlmEnrichmentSummary:
        if not self.enabled:
            raise ValueError(
                "strategy_research.llm_enrichment.enabled=false. "
                "Enable it explicitly before running enrich-alt-data."
            )

        effective_enrichment_id = (enrichment_id or self.default_enrichment_id).strip() or self.default_enrichment_id
        effective_linkage_id = (linkage_id or self.default_linkage_id).strip() or self.default_linkage_id
        effective_news_corpus_id = (news_corpus_id or self.default_news_corpus_id).strip() or self.default_news_corpus_id
        effective_reddit_corpus_id = (
            (reddit_corpus_id or self.default_reddit_corpus_id).strip() or self.default_reddit_corpus_id
        )
        effective_x_corpus_id = (x_corpus_id or self.default_x_corpus_id).strip() or self.default_x_corpus_id

        layout = LlmEnrichmentLayout.from_base(
            base_dir=self.base_dir,
            enrichment_id=effective_enrichment_id,
            checkpoint_path=checkpoint_path,
        )
        storage = LlmEnrichmentStorage(layout)
        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(
            enrichment_id=effective_enrichment_id,
            linkage_id=effective_linkage_id,
        )
        warnings: list[str] = []
        if checkpoint.linkage_id != effective_linkage_id:
            warnings.append("checkpoint linkage_id mismatch; processed keys reset")
            checkpoint.linkage_id = effective_linkage_id
            checkpoint.processed_evidence_keys = set()
            checkpoint.completed_at_utc = None
            checkpoint.updated_at_utc = datetime.now(UTC)

        records = self._load_input_records(
            linkage_id=effective_linkage_id,
            news_corpus_id=effective_news_corpus_id,
            reddit_corpus_id=effective_reddit_corpus_id,
            x_corpus_id=effective_x_corpus_id,
            warnings=warnings,
        )

        existing_output_keys = self._load_existing_output_keys(storage)
        counters = _Counters()
        started_at_utc = datetime.now(UTC)
        record_limit = max(limit_records or 0, 0)

        for index, record in enumerate(records):
            if record_limit > 0 and counters.records_processed >= record_limit:
                break
            if record.evidence_key in checkpoint.processed_evidence_keys:
                counters = _Counters(
                    records_processed=counters.records_processed,
                    records_skipped=counters.records_skipped + 1,
                    enrichments_persisted=counters.enrichments_persisted,
                    fallbacks_used=counters.fallbacks_used,
                    failures=counters.failures,
                    schema_violations=counters.schema_violations,
                )
                continue
            if record.evidence_key in existing_output_keys:
                checkpoint.processed_evidence_keys.add(record.evidence_key)
                checkpoint.updated_at_utc = datetime.now(UTC)
                storage.save_checkpoint(checkpoint)
                counters = _Counters(
                    records_processed=counters.records_processed,
                    records_skipped=counters.records_skipped + 1,
                    enrichments_persisted=counters.enrichments_persisted,
                    fallbacks_used=counters.fallbacks_used,
                    failures=counters.failures,
                    schema_violations=counters.schema_violations,
                )
                continue

            if self.throttle_sec > 0 and index > 0:
                time.sleep(self.throttle_sec)

            processed = self._process_record(
                storage=storage,
                enrichment_id=effective_enrichment_id,
                linkage_id=effective_linkage_id,
                record=record,
                counters=counters,
                warnings=warnings,
            )
            counters = processed
            existing_output_keys.add(record.evidence_key)

            checkpoint.processed_evidence_keys.add(record.evidence_key)
            checkpoint.updated_at_utc = datetime.now(UTC)
            storage.save_checkpoint(checkpoint)

        all_keys = {record.evidence_key for record in records}
        if all_keys.issubset(checkpoint.processed_evidence_keys):
            checkpoint.completed_at_utc = datetime.now(UTC)
        else:
            checkpoint.completed_at_utc = None
        checkpoint.updated_at_utc = datetime.now(UTC)
        storage.save_checkpoint(checkpoint)

        finished_at_utc = datetime.now(UTC)
        manifest = {
            "enrichment_id": effective_enrichment_id,
            "linkage_id": effective_linkage_id,
            "updated_at_utc": finished_at_utc.isoformat(),
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "records_seen": len(records),
            "processed_evidence_count": len(checkpoint.processed_evidence_keys),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "status_counts": self._status_counts(storage),
        }
        storage.write_manifest(manifest)

        logger.info(
            "strategy_research_llm_enrichment_completed",
            extra={
                "event": "strategy_research_llm_enrichment_completed",
                "enrichment_id": effective_enrichment_id,
                "linkage_id": effective_linkage_id,
                "records_seen": len(records),
                "records_processed": counters.records_processed,
                "records_skipped": counters.records_skipped,
                "enrichments_persisted": counters.enrichments_persisted,
                "fallbacks_used": counters.fallbacks_used,
                "failures": counters.failures,
                "schema_violations": counters.schema_violations,
                "checkpoint_completed": checkpoint.completed_at_utc is not None,
            },
        )

        return LlmEnrichmentSummary(
            enrichment_id=effective_enrichment_id,
            linkage_id=effective_linkage_id,
            enrichment_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            records_seen=len(records),
            records_processed=counters.records_processed,
            records_skipped=counters.records_skipped,
            enrichments_persisted=counters.enrichments_persisted,
            fallbacks_used=counters.fallbacks_used,
            failures=counters.failures,
            schema_violations=counters.schema_violations,
            warnings=tuple(warnings),
        )

    def inspect_enrichment(
        self,
        *,
        enrichment_id: str | None = None,
        linkage_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> LlmEnrichmentInspection:
        effective_enrichment_id = (enrichment_id or self.default_enrichment_id).strip() or self.default_enrichment_id
        effective_linkage_id = (linkage_id or self.default_linkage_id).strip() or self.default_linkage_id
        layout = LlmEnrichmentLayout.from_base(
            base_dir=self.base_dir,
            enrichment_id=effective_enrichment_id,
            checkpoint_path=checkpoint_path,
        )
        storage = LlmEnrichmentStorage(layout)
        checkpoint = storage.load_checkpoint(enrichment_id=effective_enrichment_id, linkage_id=effective_linkage_id)
        return LlmEnrichmentInspection(
            enrichment_id=effective_enrichment_id,
            linkage_id=checkpoint.linkage_id,
            enrichment_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            processed_evidence_count=len(checkpoint.processed_evidence_keys),
            raw_counts={name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            normalized_counts={name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            status_counts=self._status_counts(storage),
        )

    def _process_record(
        self,
        *,
        storage: LlmEnrichmentStorage,
        enrichment_id: str,
        linkage_id: str,
        record: EnrichmentInputRecord,
        counters: _Counters,
        warnings: list[str],
    ) -> _Counters:
        prompt_payload = {
            "evidence_key": record.evidence_key,
            "source_class": record.source_class,
            "source_name": record.source_name,
            "linkage_state": record.linkage_state,
            "market_id": record.market_id,
            "event_id": record.event_id,
            "market_title": record.market_title,
            "event_title": record.event_title,
            "decision_timestamp_utc": (
                record.decision_timestamp_utc.isoformat() if record.decision_timestamp_utc is not None else None
            ),
            "text": record.text,
            "url": record.url,
            "published_at_utc": record.published_at_utc.isoformat() if record.published_at_utc is not None else None,
            "allowed_tasks": [
                "relevance_classification",
                "claim_extraction",
                "novelty_classification",
                "contradiction_detection",
                "catalyst_strength_classification",
                "structured_summarization",
            ],
        }

        provider_used = self.primary_provider.provider_name
        model_id = self.primary_provider.model_id
        determinism_mode = "best_effort"
        status = "enriched"
        fallback_used = False
        failure_reason = ""
        raw_output: Mapping[str, Any] = {}

        storage.append_raw(
            "prompt_requests",
            {
                "enrichment_id": enrichment_id,
                "linkage_id": linkage_id,
                "evidence_key": record.evidence_key,
                "provider_name": provider_used,
                "model_id": model_id,
                "prompt_version": self.prompt_version,
                "requested_at_utc": datetime.now(UTC).isoformat(),
                "request": prompt_payload,
            },
        )

        try:
            raw_output = self.primary_provider.enrich(record, prompt_version=self.prompt_version)
            if provider_used.startswith("deterministic"):
                determinism_mode = "strict"
        except Exception as exc:  # pragma: no cover - fallback branch tested via fake provider
            if self.fallback_provider is not None:
                fallback_used = True
                provider_used = self.fallback_provider.provider_name
                model_id = self.fallback_provider.model_id
                determinism_mode = "strict" if provider_used.startswith("deterministic") else "best_effort"
                try:
                    raw_output = self.fallback_provider.enrich(record, prompt_version=self.prompt_version)
                    status = "fallback"
                except Exception as fallback_exc:
                    status = "failed"
                    failure_reason = type(fallback_exc).__name__
                    warnings.append(f"evidence_key={record.evidence_key} fallback_failed={type(fallback_exc).__name__}")
            else:
                status = "failed"
                failure_reason = type(exc).__name__
                warnings.append(f"evidence_key={record.evidence_key} enrichment_failed={type(exc).__name__}")

        if status == "failed":
            normalized = normalize_enrichment_output({})
        else:
            normalized = normalize_enrichment_output(raw_output)

        storage.append_raw(
            "provider_outputs",
            {
                "enrichment_id": enrichment_id,
                "linkage_id": linkage_id,
                "evidence_key": record.evidence_key,
                "provider_name": provider_used,
                "model_id": model_id,
                "prompt_version": self.prompt_version,
                "fallback_used": fallback_used,
                "status": status,
                "failure_reason": failure_reason,
                "responded_at_utc": datetime.now(UTC).isoformat(),
                "raw_output": dict(raw_output),
                "normalization_violations": list(normalized.violations),
            },
        )

        storage.append_normalized(
            "enrichment_records",
            {
                "enrichment_id": enrichment_id,
                "linkage_id": linkage_id,
                "evidence_key": record.evidence_key,
                "source_class": record.source_class,
                "source_name": record.source_name,
                "source_record_id": record.source_record_id,
                "dedup_key": record.dedup_key,
                "market_id": record.market_id,
                "event_id": record.event_id,
                "market_title": record.market_title,
                "event_title": record.event_title,
                "decision_timestamp_utc": (
                    record.decision_timestamp_utc.isoformat() if record.decision_timestamp_utc is not None else None
                ),
                "title": record.title,
                "body": record.body,
                "url": record.url,
                "published_at_utc": record.published_at_utc.isoformat() if record.published_at_utc is not None else None,
                "fetched_at_utc": record.fetched_at_utc.isoformat() if record.fetched_at_utc is not None else None,
                "linkage_state": record.linkage_state,
                "linkage_confidence": record.linkage_confidence,
                "provider_name": provider_used,
                "model_id": model_id,
                "prompt_version": self.prompt_version,
                "determinism_mode": determinism_mode,
                "status": status,
                "fallback_used": fallback_used,
                "failure_reason": failure_reason,
                "normalization_violations": list(normalized.violations),
                "output": normalized.output.to_dict(),
                "created_at_utc": datetime.now(UTC).isoformat(),
            },
        )

        schema_violations = counters.schema_violations + len(normalized.violations)
        failures = counters.failures + (1 if status == "failed" else 0)
        fallbacks_used = counters.fallbacks_used + (1 if fallback_used else 0)

        return _Counters(
            records_processed=counters.records_processed + 1,
            records_skipped=counters.records_skipped,
            enrichments_persisted=counters.enrichments_persisted + 1,
            fallbacks_used=fallbacks_used,
            failures=failures,
            schema_violations=schema_violations,
        )

    def _load_input_records(
        self,
        *,
        linkage_id: str,
        news_corpus_id: str,
        reddit_corpus_id: str,
        x_corpus_id: str,
        warnings: list[str],
    ) -> list[EnrichmentInputRecord]:
        linkage_path = self.linkage_base_dir / linkage_id / "normalized" / "linkage_results.jsonl"
        if not linkage_path.exists():
            raise FileNotFoundError(
                "Linkage dataset not found: "
                f"{linkage_path}. Run build-linkage before enrich-alt-data."
            )

        news_map = self._load_news_records(corpus_id=news_corpus_id, warnings=warnings)
        reddit_map = self._load_reddit_records(corpus_id=reddit_corpus_id, warnings=warnings)
        x_map = self._load_x_records(corpus_id=x_corpus_id, warnings=warnings)

        rows: list[EnrichmentInputRecord] = []
        with linkage_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    warnings.append(f"ignored invalid linkage JSON row line={line_number}")
                    continue
                if not isinstance(row, dict):
                    warnings.append(f"ignored non-object linkage row line={line_number}")
                    continue
                linkage_state = str(row.get("state") or "").strip().lower()
                if self.include_states and linkage_state not in self.include_states:
                    continue
                evidence_key = str(row.get("evidence_key") or "").strip()
                source_class = str(row.get("source_class") or "").strip().lower()
                source_record_id = str(row.get("source_record_id") or "").strip()
                dedup_key = str(row.get("dedup_key") or "").strip()
                if not evidence_key:
                    warnings.append(f"ignored linkage row missing evidence_key line={line_number}")
                    continue

                source_payload = self._resolve_source_payload(
                    source_class=source_class,
                    evidence_key=evidence_key,
                    source_record_id=source_record_id,
                    dedup_key=dedup_key,
                    news_map=news_map,
                    reddit_map=reddit_map,
                    x_map=x_map,
                )
                if source_payload is None:
                    warnings.append(f"source evidence not found for evidence_key={evidence_key}")
                    source_payload = {
                        "title": str(row.get("evidence_title") or "").strip(),
                        "body": "",
                        "url": str(row.get("evidence_url") or "").strip(),
                        "published_at_utc": parse_datetime_utc(row.get("published_at_utc")),
                        "fetched_at_utc": parse_datetime_utc(row.get("fetched_at_utc")),
                    }

                confidence = self._to_probability(row.get("confidence"), default=0.0)
                record = EnrichmentInputRecord(
                    evidence_key=evidence_key,
                    source_class=source_class,
                    source_name=str(row.get("source_name") or "").strip(),
                    source_record_id=source_record_id,
                    dedup_key=dedup_key,
                    linkage_state=linkage_state or "unresolved",
                    linkage_confidence=confidence,
                    market_id=str(row.get("matched_market_id") or "").strip(),
                    event_id=str(row.get("matched_event_id") or "").strip(),
                    market_title=str(row.get("matched_market_title") or "").strip(),
                    event_title=str(row.get("matched_event_title") or "").strip(),
                    decision_timestamp_utc=parse_datetime_utc(row.get("decision_timestamp_utc")),
                    title=str(source_payload.get("title") or "").strip(),
                    body=str(source_payload.get("body") or "").strip(),
                    url=str(source_payload.get("url") or "").strip(),
                    published_at_utc=parse_datetime_utc(source_payload.get("published_at_utc")),
                    fetched_at_utc=parse_datetime_utc(source_payload.get("fetched_at_utc")),
                )
                rows.append(record)

        unique: dict[str, EnrichmentInputRecord] = {}
        for row in rows:
            unique.setdefault(row.evidence_key, row)
        return sorted(
            unique.values(),
            key=lambda item: (
                item.published_at_utc or datetime(1970, 1, 1, tzinfo=UTC),
                item.evidence_key,
            ),
        )

    def _load_news_records(self, *, corpus_id: str, warnings: list[str]) -> dict[str, Mapping[str, Any]]:
        path = self.news_corpus_base_dir / corpus_id / "normalized" / "news_articles.jsonl"
        if not path.exists():
            warnings.append(f"news corpus not found at {path}")
            return {}
        rows: dict[str, Mapping[str, Any]] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "news_rss_web").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            rows[evidence_key] = {
                "title": str(row.get("title") or "").strip(),
                "body": str(row.get("summary") or "").strip(),
                "url": str(row.get("article_url") or "").strip(),
                "published_at_utc": row.get("published_at_utc"),
                "fetched_at_utc": row.get("fetched_at_utc"),
            }
        return rows

    def _load_reddit_records(self, *, corpus_id: str, warnings: list[str]) -> dict[str, Mapping[str, Any]]:
        path = self.reddit_corpus_base_dir / corpus_id / "normalized" / "reddit_evidence.jsonl"
        if not path.exists():
            warnings.append(f"reddit corpus not found at {path}")
            return {}
        rows: dict[str, Mapping[str, Any]] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "reddit").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            body = str(row.get("body") or "").strip()
            title = str(row.get("title") or "").strip()
            rows[evidence_key] = {
                "title": title,
                "body": body,
                "url": str(row.get("permalink_url") or row.get("external_url") or "").strip(),
                "published_at_utc": row.get("created_at_utc"),
                "fetched_at_utc": row.get("fetched_at_utc"),
            }
        return rows

    def _load_x_records(self, *, corpus_id: str, warnings: list[str]) -> dict[str, Mapping[str, Any]]:
        path = self.x_corpus_base_dir / corpus_id / "normalized" / "x_evidence.jsonl"
        if not path.exists():
            warnings.append(f"x corpus not found at {path}")
            return {}
        rows: dict[str, Mapping[str, Any]] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "x").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            text = str(row.get("text") or "").strip()
            rows[evidence_key] = {
                "title": text,
                "body": "",
                "url": str(row.get("post_url") or "").strip(),
                "published_at_utc": row.get("created_at_utc"),
                "fetched_at_utc": row.get("fetched_at_utc"),
            }
        return rows

    @staticmethod
    def _resolve_source_payload(
        *,
        source_class: str,
        evidence_key: str,
        source_record_id: str,
        dedup_key: str,
        news_map: Mapping[str, Mapping[str, Any]],
        reddit_map: Mapping[str, Mapping[str, Any]],
        x_map: Mapping[str, Mapping[str, Any]],
    ) -> Mapping[str, Any] | None:
        if source_class == "news_rss_web":
            source_map = news_map
        elif source_class == "reddit":
            source_map = reddit_map
        elif source_class == "x":
            source_map = x_map
        else:
            return None
        payload = source_map.get(evidence_key)
        if payload is not None:
            return payload
        fallback_key = f"{source_class}:{dedup_key or source_record_id}"
        return source_map.get(fallback_key)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if isinstance(payload, dict):
                    rows.append(payload)
        return rows

    @staticmethod
    def _to_probability(value: Any, *, default: float) -> float:
        if isinstance(value, bool) or value is None:
            return default
        if isinstance(value, (int, float)):
            numeric = float(value)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                numeric = float(text)
            except ValueError:
                return default
        else:
            return default
        return max(0.0, min(1.0, numeric))

    @staticmethod
    def _load_existing_output_keys(storage: LlmEnrichmentStorage) -> set[str]:
        keys: set[str] = set()
        for row in storage.read_rows(normalized=True, name="enrichment_records"):
            evidence_key = str(row.get("evidence_key") or "").strip()
            if evidence_key:
                keys.add(evidence_key)
        return keys

    @staticmethod
    def _status_counts(storage: LlmEnrichmentStorage) -> dict[str, int]:
        counts: dict[str, int] = {"enriched": 0, "fallback": 0, "failed": 0}
        for row in storage.read_rows(normalized=True, name="enrichment_records"):
            status = str(row.get("status") or "").strip().lower()
            if status in counts:
                counts[status] += 1
        return counts
