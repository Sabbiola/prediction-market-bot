from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from prediction_market_bot.domain.enums import MarketStatus
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.infrastructure import StructuredHttpResearchSource

from .models import (
    MarketDecisionPoint,
    ResearchAlignmentVerification,
    ResearchCorpusBackfillSummary,
    ResearchCorpusInspection,
    parse_datetime_utc,
)
from .storage import CorpusLayout, CorpusStorage, NORMALIZED_FILES, RAW_FILES

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _Counters:
    decision_points_processed: int = 0
    decision_points_skipped: int = 0
    source_batches: int = 0
    raw_payloads_persisted: int = 0
    normalized_findings_persisted: int = 0
    normalized_findings_deduped: int = 0
    findings_filtered_missing_published_at: int = 0
    findings_filtered_post_decision: int = 0


class ResearchEvidenceArchivalService:
    def __init__(
        self,
        *,
        base_dir: Path,
        source_dataset_base_dir: Path,
        default_corpus_id: str,
        default_source_dataset_id: str,
        sources: Sequence[StructuredHttpResearchSource],
        default_limit_per_source: int,
        throttle_sec: float,
        require_published_at: bool,
        drop_unaligned: bool,
    ) -> None:
        self.base_dir = base_dir
        self.source_dataset_base_dir = source_dataset_base_dir
        self.default_corpus_id = default_corpus_id
        self.default_source_dataset_id = default_source_dataset_id
        self.sources = tuple(sources)
        self.default_limit_per_source = max(default_limit_per_source, 1)
        self.throttle_sec = max(throttle_sec, 0.0)
        self.require_published_at = require_published_at
        self.drop_unaligned = drop_unaligned

    def backfill_research_evidence(
        self,
        *,
        corpus_id: str | None = None,
        source_dataset_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        limit_markets: int | None = None,
        limit_per_source: int | None = None,
        decision_date_from: date | None = None,
        decision_date_to: date | None = None,
    ) -> ResearchCorpusBackfillSummary:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        effective_source_dataset_id = (
            source_dataset_id or self.default_source_dataset_id
        ).strip() or self.default_source_dataset_id
        layout = CorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = CorpusStorage(layout)
        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(
            corpus_id=effective_corpus_id,
            source_dataset_id=effective_source_dataset_id,
        )
        warnings: list[str] = []
        if checkpoint.source_dataset_id != effective_source_dataset_id:
            warnings.append("checkpoint source_dataset_id mismatch; processed ids reset")
            checkpoint.source_dataset_id = effective_source_dataset_id
            checkpoint.processed_market_ids = set()
            checkpoint.completed_at_utc = None
            checkpoint.updated_at_utc = datetime.now(UTC)

        decisions = self._load_market_decisions(
            source_dataset_id=effective_source_dataset_id,
            decision_date_from=decision_date_from,
            decision_date_to=decision_date_to,
            warnings=warnings,
        )
        source_limit = max(limit_per_source if limit_per_source is not None else self.default_limit_per_source, 1)
        market_limit = max(limit_markets if limit_markets is not None else 0, 0)
        started_at = datetime.now(UTC)
        counters = _Counters()

        existing_raw_keys = self._load_existing_raw_keys(storage)
        existing_normalized_keys = self._load_existing_normalized_keys(storage)
        existing_market_decisions = self._load_existing_market_decision_keys(storage)

        for decision in decisions:
            if market_limit > 0 and counters.decision_points_processed >= market_limit:
                break
            if decision.market_id in checkpoint.processed_market_ids:
                counters = _Counters(
                    decision_points_processed=counters.decision_points_processed,
                    decision_points_skipped=counters.decision_points_skipped + 1,
                    source_batches=counters.source_batches,
                    raw_payloads_persisted=counters.raw_payloads_persisted,
                    normalized_findings_persisted=counters.normalized_findings_persisted,
                    normalized_findings_deduped=counters.normalized_findings_deduped,
                    findings_filtered_missing_published_at=counters.findings_filtered_missing_published_at,
                    findings_filtered_post_decision=counters.findings_filtered_post_decision,
                )
                continue

            counters = self._process_decision_point(
                storage=storage,
                decision=decision,
                source_dataset_id=effective_source_dataset_id,
                source_limit=source_limit,
                existing_raw_keys=existing_raw_keys,
                existing_normalized_keys=existing_normalized_keys,
                existing_market_decisions=existing_market_decisions,
                counters=counters,
                warnings=warnings,
            )
            checkpoint.processed_market_ids.add(decision.market_id)
            checkpoint.updated_at_utc = datetime.now(UTC)
            storage.save_checkpoint(checkpoint)

        known_market_ids = {decision.market_id for decision in decisions}
        if known_market_ids.issubset(checkpoint.processed_market_ids):
            checkpoint.completed_at_utc = datetime.now(UTC)
        else:
            checkpoint.completed_at_utc = None
        checkpoint.updated_at_utc = datetime.now(UTC)
        storage.save_checkpoint(checkpoint)

        finished_at = datetime.now(UTC)
        manifest = {
            "corpus_id": effective_corpus_id,
            "source_dataset_id": effective_source_dataset_id,
            "updated_at_utc": finished_at.isoformat(),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "decision_points_seen": len(decisions),
            "processed_market_count": len(checkpoint.processed_market_ids),
        }
        storage.write_manifest(manifest)
        logger.info(
            "research_evidence_backfill_completed",
            extra={
                "event": "research_evidence_backfill_completed",
                "corpus_id": effective_corpus_id,
                "source_dataset_id": effective_source_dataset_id,
                "decision_points_seen": len(decisions),
                "decision_points_processed": counters.decision_points_processed,
                "decision_points_skipped": counters.decision_points_skipped,
                "source_batches": counters.source_batches,
                "raw_payloads_persisted": counters.raw_payloads_persisted,
                "normalized_findings_persisted": counters.normalized_findings_persisted,
                "normalized_findings_deduped": counters.normalized_findings_deduped,
                "findings_filtered_missing_published_at": counters.findings_filtered_missing_published_at,
                "findings_filtered_post_decision": counters.findings_filtered_post_decision,
                "checkpoint_completed": checkpoint.completed_at_utc is not None,
            },
        )
        return ResearchCorpusBackfillSummary(
            corpus_id=effective_corpus_id,
            source_dataset_id=effective_source_dataset_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            decision_points_seen=len(decisions),
            decision_points_processed=counters.decision_points_processed,
            decision_points_skipped=counters.decision_points_skipped,
            source_batches=counters.source_batches,
            raw_payloads_persisted=counters.raw_payloads_persisted,
            normalized_findings_persisted=counters.normalized_findings_persisted,
            normalized_findings_deduped=counters.normalized_findings_deduped,
            findings_filtered_missing_published_at=counters.findings_filtered_missing_published_at,
            findings_filtered_post_decision=counters.findings_filtered_post_decision,
            warnings=tuple(warnings),
        )

    def inspect_corpus(
        self,
        *,
        corpus_id: str | None = None,
        source_dataset_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> ResearchCorpusInspection:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        effective_source_dataset_id = (
            source_dataset_id or self.default_source_dataset_id
        ).strip() or self.default_source_dataset_id
        layout = CorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = CorpusStorage(layout)
        checkpoint = storage.load_checkpoint(
            corpus_id=effective_corpus_id,
            source_dataset_id=effective_source_dataset_id,
        )
        return ResearchCorpusInspection(
            corpus_id=effective_corpus_id,
            source_dataset_id=checkpoint.source_dataset_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            processed_market_count=len(checkpoint.processed_market_ids),
            raw_counts={name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            normalized_counts={name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
        )

    def verify_alignment(
        self,
        *,
        corpus_id: str | None = None,
        source_dataset_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> ResearchAlignmentVerification:
        inspection = self.inspect_corpus(
            corpus_id=corpus_id,
            source_dataset_id=source_dataset_id,
            checkpoint_path=checkpoint_path,
        )
        layout = CorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=inspection.corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = CorpusStorage(layout)
        evidence_rows = storage.read_rows(normalized=True, name="evidence_findings")
        market_rows = storage.read_rows(normalized=True, name="market_decisions")

        errors: list[str] = []
        warnings: list[str] = []
        if not evidence_rows:
            warnings.append("normalized/evidence_findings.jsonl is empty")

        market_keys: set[tuple[str, str]] = set()
        for market_row in market_rows:
            market_id = str(market_row.get("market_id") or "").strip()
            decision_ts_text = str(market_row.get("decision_timestamp_utc") or "").strip()
            if market_id and decision_ts_text:
                market_keys.add((market_id, decision_ts_text))

        seen_findings: set[tuple[str, str, str, str]] = set()
        duplicate_findings = 0
        for row in evidence_rows:
            market_id = str(row.get("market_id") or "").strip()
            source_name = str(row.get("source_name") or "").strip()
            source_record_id = str(row.get("source_record_id") or "").strip()
            finding_identity = str(row.get("finding_identity") or "").strip()
            decision_text = str(row.get("decision_timestamp_utc") or "").strip()

            if not market_id:
                errors.append("missing market_id in evidence row")
                continue
            if not source_name:
                errors.append(f"market={market_id}: missing source_name")
            if not source_record_id:
                errors.append(f"market={market_id}: missing source_record_id")
            if not finding_identity:
                errors.append(f"market={market_id}: missing finding_identity")
            if not decision_text:
                errors.append(f"market={market_id}: missing decision_timestamp_utc")
                continue
            decision_ts = parse_datetime_utc(decision_text)
            if decision_ts is None:
                errors.append(f"market={market_id}: invalid decision_timestamp_utc")
                continue
            published_text = str(row.get("published_at_utc") or "").strip()
            published_ts = parse_datetime_utc(published_text)
            if published_ts is None:
                errors.append(f"market={market_id}: missing_or_invalid published_at_utc")
            elif published_ts > decision_ts:
                errors.append(
                    f"market={market_id}: post-decision evidence "
                    f"(published_at_utc={published_ts.isoformat()}, decision_timestamp_utc={decision_ts.isoformat()})"
                )

            provenance = row.get("provenance")
            if not isinstance(provenance, list) or not provenance:
                errors.append(f"market={market_id}: missing provenance")

            key = (market_id, decision_text, source_name, finding_identity)
            if key in seen_findings:
                duplicate_findings += 1
            else:
                seen_findings.add(key)

            if (market_id, decision_text) not in market_keys:
                errors.append(f"market={market_id}: evidence has no matching market_decisions row")

        if duplicate_findings > 0:
            errors.append(f"duplicate evidence findings keys={duplicate_findings}")

        details = {
            "corpus_root": str(inspection.corpus_root),
            "checkpoint_path": str(inspection.checkpoint_path),
            "checkpoint_completed": inspection.checkpoint_completed,
            "raw_counts": dict(inspection.raw_counts),
            "normalized_counts": dict(inspection.normalized_counts),
            "market_decision_rows": len(market_rows),
            "evidence_rows": len(evidence_rows),
            "duplicate_finding_keys": duplicate_findings,
        }
        return ResearchAlignmentVerification(
            corpus_id=inspection.corpus_id,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _process_decision_point(
        self,
        *,
        storage: CorpusStorage,
        decision: MarketDecisionPoint,
        source_dataset_id: str,
        source_limit: int,
        existing_raw_keys: set[tuple[str, str, str, str]],
        existing_normalized_keys: set[tuple[str, str, str, str]],
        existing_market_decisions: set[tuple[str, str]],
        counters: _Counters,
        warnings: list[str],
    ) -> _Counters:
        decision_iso = decision.decision_timestamp_utc.isoformat()
        market_decision_key = (decision.market_id, decision_iso)
        if market_decision_key not in existing_market_decisions:
            storage.append_normalized(
                "market_decisions",
                {
                    **decision.to_dict(),
                    "source_dataset_id": source_dataset_id,
                    "ingested_at_utc": datetime.now(UTC).isoformat(),
                },
            )
            existing_market_decisions.add(market_decision_key)

        market = self._build_market_snapshot(decision)
        source_batches = counters.source_batches
        raw_payloads = counters.raw_payloads_persisted
        normalized_persisted = counters.normalized_findings_persisted
        normalized_deduped = counters.normalized_findings_deduped
        filtered_missing_published_at = counters.findings_filtered_missing_published_at
        filtered_post_decision = counters.findings_filtered_post_decision

        for source in self.sources:
            if self.throttle_sec > 0:
                time.sleep(self.throttle_sec)
            try:
                batch = source.fetch_with_meta(market, limit=source_limit)
            except Exception as exc:
                warnings.append(
                    f"market={decision.market_id} source={source.source_name} fetch_failed={type(exc).__name__}"
                )
                continue

            source_batches += 1
            for record in batch.raw_records:
                source_record_id = self._source_record_id(record, source_name=batch.source_name)
                raw_key = (decision.market_id, decision_iso, batch.source_name, source_record_id)
                if raw_key in existing_raw_keys:
                    continue
                existing_raw_keys.add(raw_key)
                raw_payloads += 1
                storage.append_raw(
                    "source_payloads",
                    {
                        "market_id": decision.market_id,
                        "event_id": decision.event_id,
                        "decision_timestamp_utc": decision_iso,
                        "source_dataset_id": source_dataset_id,
                        "source_name": batch.source_name,
                        "source_type": batch.source_type.value,
                        "query": batch.query,
                        "source_record_id": source_record_id,
                        "fetched_at_utc": datetime.now(UTC).isoformat(),
                        "payload": dict(record),
                    },
                )

            for finding in batch.findings:
                published_at = self._extract_provenance_timestamp(finding.provenance, prefix="published_at=")
                fetched_at = self._extract_provenance_timestamp(finding.provenance, prefix="fetched_at=")
                aligned, reason = self._evaluate_alignment(
                    decision_timestamp=decision.decision_timestamp_utc,
                    published_at=published_at,
                )
                if reason == "missing_published_at":
                    filtered_missing_published_at += 1
                elif reason == "post_decision":
                    filtered_post_decision += 1

                source_record_id = self._source_record_id_for_finding(finding)
                finding_identity = self._finding_identity(finding=finding, published_at=published_at)
                normalized_key = (decision.market_id, decision_iso, finding.source_name, finding_identity)
                if normalized_key in existing_normalized_keys:
                    normalized_deduped += 1
                    continue

                should_skip = False
                if self.require_published_at and published_at is None:
                    should_skip = True
                if self.drop_unaligned and not aligned:
                    should_skip = True
                if should_skip:
                    continue

                existing_normalized_keys.add(normalized_key)
                normalized_persisted += 1
                storage.append_normalized(
                    "evidence_findings",
                    {
                        "market_id": decision.market_id,
                        "event_id": decision.event_id,
                        "decision_timestamp_utc": decision_iso,
                        "source_dataset_id": source_dataset_id,
                        "source_name": finding.source_name,
                        "source_type": finding.source_type.value,
                        "query": self._extract_query(finding.provenance),
                        "source_record_id": source_record_id,
                        "finding_identity": finding_identity,
                        "summary": finding.summary,
                        "sentiment": finding.sentiment,
                        "credibility": finding.credibility,
                        "url": finding.url,
                        "provenance": list(finding.provenance),
                        "published_at_utc": published_at.isoformat() if published_at is not None else None,
                        "fetched_at_utc": fetched_at.isoformat() if fetched_at is not None else None,
                        "is_time_aligned": aligned,
                        "alignment_reason": reason,
                        "resolved_outcome": decision.resolved_outcome,
                    },
                )

        return _Counters(
            decision_points_processed=counters.decision_points_processed + 1,
            decision_points_skipped=counters.decision_points_skipped,
            source_batches=source_batches,
            raw_payloads_persisted=raw_payloads,
            normalized_findings_persisted=normalized_persisted,
            normalized_findings_deduped=normalized_deduped,
            findings_filtered_missing_published_at=filtered_missing_published_at,
            findings_filtered_post_decision=filtered_post_decision,
        )

    def _load_market_decisions(
        self,
        *,
        source_dataset_id: str,
        decision_date_from: date | None,
        decision_date_to: date | None,
        warnings: list[str],
    ) -> list[MarketDecisionPoint]:
        normalized_markets = self.source_dataset_base_dir / source_dataset_id / "normalized" / "markets.jsonl"
        if not normalized_markets.exists():
            raise FileNotFoundError(
                "Historical normalized markets dataset not found: "
                f"{normalized_markets}. Run backfill-historical-markets first."
            )
        decisions: list[MarketDecisionPoint] = []
        with normalized_markets.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    warnings.append(f"ignored invalid JSON row in markets dataset at line={line_number}")
                    continue
                if not isinstance(row, dict):
                    warnings.append(f"ignored non-object row in markets dataset at line={line_number}")
                    continue
                market_id = str(row.get("market_id") or "").strip()
                if not market_id:
                    warnings.append(f"ignored market row without market_id at line={line_number}")
                    continue
                decision_ts = parse_datetime_utc(row.get("close_at_utc")) or parse_datetime_utc(row.get("resolved_at_utc"))
                if decision_ts is None:
                    warnings.append(f"ignored market row without decision timestamp market_id={market_id}")
                    continue
                if decision_date_from is not None and decision_ts.date() < decision_date_from:
                    continue
                if decision_date_to is not None and decision_ts.date() > decision_date_to:
                    continue
                decisions.append(
                    MarketDecisionPoint(
                        market_id=market_id,
                        event_id=str(row.get("event_id") or "").strip(),
                        market_title=str(row.get("question") or "").strip(),
                        market_category=str(row.get("category") or "").strip(),
                        decision_timestamp_utc=decision_ts,
                        resolved_outcome=str(row.get("resolved_outcome") or "").strip().upper(),
                        resolved_at_utc=parse_datetime_utc(row.get("resolved_at_utc")),
                        yes_price_last=self._to_float(row.get("yes_price_last")),
                        liquidity_usd=self._to_float(row.get("liquidity_usd")),
                        volume_24h_usd=self._to_float(row.get("volume_24h_usd")),
                    )
                )
        return sorted(decisions, key=lambda item: (item.decision_timestamp_utc, item.market_id))

    @staticmethod
    def _build_market_snapshot(decision: MarketDecisionPoint) -> MarketSnapshot:
        yes_price = decision.yes_price_last if decision.yes_price_last is not None else 0.5
        yes_price = min(max(yes_price, 0.0), 1.0)
        liquidity = max(decision.liquidity_usd or 0.0, 0.0)
        volume_24h = max(decision.volume_24h_usd or 0.0, 0.0)
        return MarketSnapshot.from_yes_price(
            market_id=decision.market_id,
            venue="offline_research_corpus",
            title=decision.market_title or decision.market_id,
            yes_price=yes_price,
            liquidity_usd=liquidity,
            volume_24h_usd=volume_24h,
            spread_bps=0,
            hours_to_resolution=0.0,
            last_price_move_bps=0,
            category=decision.market_category or "general",
            status=MarketStatus.RESOLVED,
            updated_at=decision.decision_timestamp_utc,
        )

    @staticmethod
    def _evaluate_alignment(*, decision_timestamp: datetime, published_at: datetime | None) -> tuple[bool, str]:
        if published_at is None:
            return False, "missing_published_at"
        if published_at > decision_timestamp:
            return False, "post_decision"
        return True, "aligned"

    @staticmethod
    def _source_record_id(record: Mapping[str, Any], *, source_name: str) -> str:
        for key in ("id", "record_id", "uid", "uuid", "doi", "work_id", "pmid", "url", "permalink", "slug"):
            value = record.get(key)
            if isinstance(value, (str, int)):
                text = str(value).strip()
                if text:
                    return f"{source_name}:{text}"
        canonical = json.dumps(dict(record), sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{source_name}:sha256:{digest}"

    @classmethod
    def _source_record_id_for_finding(cls, finding: ResearchFinding) -> str:
        if finding.url:
            return f"{finding.source_name}:{finding.url.strip()}"
        return cls._finding_identity(finding=finding, published_at=cls._extract_provenance_timestamp(finding.provenance, prefix="published_at="))

    @staticmethod
    def _finding_identity(*, finding: ResearchFinding, published_at: datetime | None) -> str:
        canonical = {
            "source_name": finding.source_name,
            "source_type": finding.source_type.value,
            "url": finding.url,
            "summary": finding.summary,
            "published_at_utc": published_at.isoformat() if published_at is not None else "",
        }
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()
        return f"finding:sha256:{digest}"

    @staticmethod
    def _extract_provenance_timestamp(provenance: Iterable[str], *, prefix: str) -> datetime | None:
        for item in provenance:
            text = item.strip()
            if not text.startswith(prefix):
                continue
            return parse_datetime_utc(text[len(prefix) :].strip())
        return None

    @staticmethod
    def _extract_query(provenance: Iterable[str]) -> str:
        for item in provenance:
            text = item.strip()
            if text.startswith("query="):
                return text[len("query=") :].strip()
        return ""

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    @staticmethod
    def _load_existing_raw_keys(storage: CorpusStorage) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in storage.read_rows(normalized=False, name="source_payloads"):
            market_id = str(row.get("market_id") or "").strip()
            decision_ts = str(row.get("decision_timestamp_utc") or "").strip()
            source_name = str(row.get("source_name") or "").strip()
            source_record_id = str(row.get("source_record_id") or "").strip()
            if market_id and decision_ts and source_name and source_record_id:
                keys.add((market_id, decision_ts, source_name, source_record_id))
        return keys

    @staticmethod
    def _load_existing_normalized_keys(storage: CorpusStorage) -> set[tuple[str, str, str, str]]:
        keys: set[tuple[str, str, str, str]] = set()
        for row in storage.read_rows(normalized=True, name="evidence_findings"):
            market_id = str(row.get("market_id") or "").strip()
            decision_ts = str(row.get("decision_timestamp_utc") or "").strip()
            source_name = str(row.get("source_name") or "").strip()
            finding_identity = str(row.get("finding_identity") or "").strip()
            if market_id and decision_ts and source_name and finding_identity:
                keys.add((market_id, decision_ts, source_name, finding_identity))
        return keys

    @staticmethod
    def _load_existing_market_decision_keys(storage: CorpusStorage) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for row in storage.read_rows(normalized=True, name="market_decisions"):
            market_id = str(row.get("market_id") or "").strip()
            decision_ts = str(row.get("decision_timestamp_utc") or "").strip()
            if market_id and decision_ts:
                keys.add((market_id, decision_ts))
        return keys
