from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    EvidenceRecord,
    LinkageBuildSummary,
    LinkageCandidateScore,
    LinkageInspection,
    LinkageQualityVerification,
    LinkageResult,
    LinkageState,
    MarketReference,
    parse_datetime_utc,
)
from .storage import LinkageLayout, LinkageStorage, NORMALIZED_FILES, RAW_FILES

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'&\-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
}


@dataclass(slots=True, frozen=True)
class _LinkageDecision:
    result: LinkageResult
    candidates_for_raw: tuple[LinkageCandidateScore, ...]


@dataclass(slots=True, frozen=True)
class _BuildCounters:
    evidence_processed: int = 0
    evidence_skipped: int = 0
    candidate_rows_persisted: int = 0
    candidate_rows_deduped: int = 0
    linked_count: int = 0
    ambiguous_count: int = 0
    unresolved_count: int = 0
    stale_count: int = 0


class EvidenceMarketLinkageService:
    def __init__(
        self,
        *,
        base_dir: Path,
        historical_base_dir: Path,
        news_corpus_base_dir: Path,
        reddit_corpus_base_dir: Path,
        x_corpus_base_dir: Path,
        default_linkage_id: str,
        default_dataset_id: str,
        default_news_corpus_id: str,
        default_reddit_corpus_id: str,
        default_x_corpus_id: str,
        enabled_source_classes: Sequence[str],
        max_staleness_days: int,
        similarity_threshold: float,
        ambiguity_margin: float,
        max_candidates_per_evidence: int,
        alias_map: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.base_dir = base_dir
        self.historical_base_dir = historical_base_dir
        self.news_corpus_base_dir = news_corpus_base_dir
        self.reddit_corpus_base_dir = reddit_corpus_base_dir
        self.x_corpus_base_dir = x_corpus_base_dir
        self.default_linkage_id = default_linkage_id
        self.default_dataset_id = default_dataset_id
        self.default_news_corpus_id = default_news_corpus_id
        self.default_reddit_corpus_id = default_reddit_corpus_id
        self.default_x_corpus_id = default_x_corpus_id
        self.enabled_source_classes = tuple(source.strip().lower() for source in enabled_source_classes if source.strip())
        self.max_staleness_days = max(max_staleness_days, 0)
        self.similarity_threshold = max(min(similarity_threshold, 1.0), 0.0)
        self.ambiguity_margin = max(min(ambiguity_margin, 1.0), 0.0)
        self.max_candidates_per_evidence = max(max_candidates_per_evidence, 1)
        self.alias_map = self._normalize_alias_map(alias_map or {})
        self.alias_lookup = self._build_alias_lookup(self.alias_map)

    def build_linkage(
        self,
        *,
        linkage_id: str | None = None,
        dataset_id: str | None = None,
        news_corpus_id: str | None = None,
        reddit_corpus_id: str | None = None,
        x_corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        limit_evidence: int | None = None,
        decision_date_from: date | None = None,
        decision_date_to: date | None = None,
    ) -> LinkageBuildSummary:
        effective_linkage_id = (linkage_id or self.default_linkage_id).strip() or self.default_linkage_id
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        effective_news_corpus_id = (news_corpus_id or self.default_news_corpus_id).strip() or self.default_news_corpus_id
        effective_reddit_corpus_id = (
            (reddit_corpus_id or self.default_reddit_corpus_id).strip() or self.default_reddit_corpus_id
        )
        effective_x_corpus_id = (x_corpus_id or self.default_x_corpus_id).strip() or self.default_x_corpus_id

        layout = LinkageLayout.from_base(
            base_dir=self.base_dir,
            linkage_id=effective_linkage_id,
            checkpoint_path=checkpoint_path,
        )
        storage = LinkageStorage(layout)
        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(linkage_id=effective_linkage_id, dataset_id=effective_dataset_id)
        warnings: list[str] = []
        if checkpoint.dataset_id != effective_dataset_id:
            warnings.append("checkpoint dataset_id mismatch; processed keys reset")
            checkpoint.dataset_id = effective_dataset_id
            checkpoint.processed_evidence_keys = set()
            checkpoint.completed_at_utc = None
            checkpoint.updated_at_utc = datetime.now(UTC)

        markets = self._load_markets(
            dataset_id=effective_dataset_id,
            decision_date_from=decision_date_from,
            decision_date_to=decision_date_to,
            warnings=warnings,
        )
        evidence = self._load_evidence(
            news_corpus_id=effective_news_corpus_id,
            reddit_corpus_id=effective_reddit_corpus_id,
            x_corpus_id=effective_x_corpus_id,
            warnings=warnings,
        )

        existing_results = self._load_existing_result_keys(storage)
        existing_candidate_keys = self._load_existing_candidate_keys(storage)
        evidence_limit = max(limit_evidence or 0, 0)
        started_at_utc = datetime.now(UTC)
        counters = _BuildCounters()

        for record in evidence:
            if evidence_limit > 0 and counters.evidence_processed >= evidence_limit:
                break
            if record.evidence_key in checkpoint.processed_evidence_keys:
                counters = _BuildCounters(
                    evidence_processed=counters.evidence_processed,
                    evidence_skipped=counters.evidence_skipped + 1,
                    candidate_rows_persisted=counters.candidate_rows_persisted,
                    candidate_rows_deduped=counters.candidate_rows_deduped,
                    linked_count=counters.linked_count,
                    ambiguous_count=counters.ambiguous_count,
                    unresolved_count=counters.unresolved_count,
                    stale_count=counters.stale_count,
                )
                continue

            decision = self._link_one_evidence(
                record=record,
                markets=markets,
                linkage_id=effective_linkage_id,
                dataset_id=effective_dataset_id,
            )
            if record.evidence_key not in existing_results:
                storage.append_normalized("linkage_results", decision.result.to_dict())
                existing_results.add(record.evidence_key)

            candidate_rows_persisted = counters.candidate_rows_persisted
            candidate_rows_deduped = counters.candidate_rows_deduped
            for candidate in decision.candidates_for_raw:
                candidate_key = (record.evidence_key, candidate.market_id)
                if candidate_key in existing_candidate_keys:
                    candidate_rows_deduped += 1
                    continue
                existing_candidate_keys.add(candidate_key)
                candidate_rows_persisted += 1
                storage.append_raw(
                    "linkage_candidates",
                    {
                        "linkage_id": effective_linkage_id,
                        "dataset_id": effective_dataset_id,
                        "evidence_key": record.evidence_key,
                        "source_class": record.source_class,
                        "source_name": record.source_name,
                        "source_record_id": record.source_record_id,
                        "dedup_key": record.dedup_key,
                        "evidence_title": record.title,
                        "evidence_url": record.url,
                        "published_at_utc": (
                            record.published_at_utc.isoformat() if record.published_at_utc is not None else None
                        ),
                        "candidate": candidate.to_dict(),
                        "scored_at_utc": datetime.now(UTC).isoformat(),
                    },
                )

            linked_count = counters.linked_count
            ambiguous_count = counters.ambiguous_count
            unresolved_count = counters.unresolved_count
            stale_count = counters.stale_count
            if decision.result.state == LinkageState.LINKED:
                linked_count += 1
            elif decision.result.state == LinkageState.AMBIGUOUS:
                ambiguous_count += 1
            elif decision.result.state == LinkageState.STALE_EVIDENCE:
                stale_count += 1
            else:
                unresolved_count += 1

            counters = _BuildCounters(
                evidence_processed=counters.evidence_processed + 1,
                evidence_skipped=counters.evidence_skipped,
                candidate_rows_persisted=candidate_rows_persisted,
                candidate_rows_deduped=candidate_rows_deduped,
                linked_count=linked_count,
                ambiguous_count=ambiguous_count,
                unresolved_count=unresolved_count,
                stale_count=stale_count,
            )
            checkpoint.processed_evidence_keys.add(record.evidence_key)
            checkpoint.updated_at_utc = datetime.now(UTC)
            storage.save_checkpoint(checkpoint)

        all_evidence_keys = {row.evidence_key for row in evidence}
        if all_evidence_keys.issubset(checkpoint.processed_evidence_keys):
            checkpoint.completed_at_utc = datetime.now(UTC)
        else:
            checkpoint.completed_at_utc = None
        checkpoint.updated_at_utc = datetime.now(UTC)
        storage.save_checkpoint(checkpoint)

        finished_at_utc = datetime.now(UTC)
        manifest = {
            "linkage_id": effective_linkage_id,
            "dataset_id": effective_dataset_id,
            "updated_at_utc": finished_at_utc.isoformat(),
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "evidence_seen": len(evidence),
            "processed_evidence_count": len(checkpoint.processed_evidence_keys),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "state_counts": self._state_counts(storage),
        }
        storage.write_manifest(manifest)
        logger.info(
            "evidence_linkage_build_completed",
            extra={
                "event": "evidence_linkage_build_completed",
                "linkage_id": effective_linkage_id,
                "dataset_id": effective_dataset_id,
                "evidence_seen": len(evidence),
                "evidence_processed": counters.evidence_processed,
                "evidence_skipped": counters.evidence_skipped,
                "candidate_rows_persisted": counters.candidate_rows_persisted,
                "candidate_rows_deduped": counters.candidate_rows_deduped,
                "linked_count": counters.linked_count,
                "ambiguous_count": counters.ambiguous_count,
                "unresolved_count": counters.unresolved_count,
                "stale_count": counters.stale_count,
                "checkpoint_completed": checkpoint.completed_at_utc is not None,
            },
        )
        return LinkageBuildSummary(
            linkage_id=effective_linkage_id,
            dataset_id=effective_dataset_id,
            linkage_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            evidence_seen=len(evidence),
            evidence_processed=counters.evidence_processed,
            evidence_skipped=counters.evidence_skipped,
            candidate_rows_persisted=counters.candidate_rows_persisted,
            candidate_rows_deduped=counters.candidate_rows_deduped,
            linked_count=counters.linked_count,
            ambiguous_count=counters.ambiguous_count,
            unresolved_count=counters.unresolved_count,
            stale_count=counters.stale_count,
            warnings=tuple(warnings),
        )

    def inspect_linkage(
        self,
        *,
        linkage_id: str | None = None,
        dataset_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> LinkageInspection:
        effective_linkage_id = (linkage_id or self.default_linkage_id).strip() or self.default_linkage_id
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = LinkageLayout.from_base(
            base_dir=self.base_dir,
            linkage_id=effective_linkage_id,
            checkpoint_path=checkpoint_path,
        )
        storage = LinkageStorage(layout)
        checkpoint = storage.load_checkpoint(linkage_id=effective_linkage_id, dataset_id=effective_dataset_id)
        return LinkageInspection(
            linkage_id=effective_linkage_id,
            dataset_id=checkpoint.dataset_id,
            linkage_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            processed_evidence_count=len(checkpoint.processed_evidence_keys),
            raw_counts={name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            normalized_counts={name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            state_counts=self._state_counts(storage),
        )

    def verify_linkage_quality(
        self,
        *,
        linkage_id: str | None = None,
        dataset_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> LinkageQualityVerification:
        inspection = self.inspect_linkage(linkage_id=linkage_id, dataset_id=dataset_id, checkpoint_path=checkpoint_path)
        layout = LinkageLayout.from_base(
            base_dir=self.base_dir,
            linkage_id=inspection.linkage_id,
            checkpoint_path=checkpoint_path,
        )
        storage = LinkageStorage(layout)
        rows = storage.read_rows(normalized=True, name="linkage_results")
        errors: list[str] = []
        warnings: list[str] = []

        if not rows:
            warnings.append("normalized/linkage_results.jsonl is empty")

        seen_keys: set[str] = set()
        duplicate_keys = 0
        linked_without_market = 0
        ambiguous_without_candidates = 0
        stale_without_timestamp = 0
        for row in rows:
            evidence_key = str(row.get("evidence_key") or "").strip()
            state = str(row.get("state") or "").strip().lower()
            confidence = row.get("confidence")
            top_candidates = row.get("top_candidates")
            published_at = parse_datetime_utc(row.get("published_at_utc"))
            if not evidence_key:
                errors.append("missing evidence_key")
                continue
            if evidence_key in seen_keys:
                duplicate_keys += 1
            else:
                seen_keys.add(evidence_key)
            if state not in {item.value for item in LinkageState}:
                errors.append(f"evidence_key={evidence_key}: invalid state={state or 'missing'}")
            if not isinstance(confidence, (int, float)):
                errors.append(f"evidence_key={evidence_key}: invalid confidence")
            if state == LinkageState.LINKED.value:
                market_id = str(row.get("matched_market_id") or "").strip()
                decision_ts = parse_datetime_utc(row.get("decision_timestamp_utc"))
                if not market_id or decision_ts is None:
                    linked_without_market += 1
            if state == LinkageState.AMBIGUOUS.value:
                if not isinstance(top_candidates, list) or len(top_candidates) < 2:
                    ambiguous_without_candidates += 1
            if state == LinkageState.STALE_EVIDENCE.value and published_at is None:
                stale_without_timestamp += 1

        if duplicate_keys > 0:
            errors.append(f"duplicate evidence_key rows={duplicate_keys}")
        if linked_without_market > 0:
            errors.append(f"linked rows without matched market={linked_without_market}")
        if ambiguous_without_candidates > 0:
            errors.append(f"ambiguous rows without >=2 candidates={ambiguous_without_candidates}")
        if stale_without_timestamp > 0:
            warnings.append(f"stale rows without published_at_utc={stale_without_timestamp}")

        details = {
            "linkage_root": str(inspection.linkage_root),
            "checkpoint_path": str(inspection.checkpoint_path),
            "checkpoint_completed": inspection.checkpoint_completed,
            "processed_evidence_count": inspection.processed_evidence_count,
            "raw_counts": dict(inspection.raw_counts),
            "normalized_counts": dict(inspection.normalized_counts),
            "state_counts": dict(inspection.state_counts),
            "duplicate_evidence_keys": duplicate_keys,
            "linked_without_market": linked_without_market,
            "ambiguous_without_candidates": ambiguous_without_candidates,
            "stale_without_timestamp": stale_without_timestamp,
        }
        return LinkageQualityVerification(
            linkage_id=inspection.linkage_id,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _link_one_evidence(
        self,
        *,
        record: EvidenceRecord,
        markets: Sequence[MarketReference],
        linkage_id: str,
        dataset_id: str,
    ) -> _LinkageDecision:
        evidence_title = record.title.strip() or record.searchable_text
        evidence_text = record.searchable_text
        evidence_tokens = self._tokenize(evidence_text)
        extracted_entities = tuple(self._extract_entities(evidence_text))

        scored_candidates = [
            self._score_candidate(
                record=record,
                market=market,
                evidence_tokens=evidence_tokens,
                evidence_title=evidence_title,
            )
            for market in markets
        ]
        scored_candidates.sort(key=lambda item: item.final_score, reverse=True)
        above_threshold = [row for row in scored_candidates if row.final_score >= self.similarity_threshold]
        time_valid_candidates = [row for row in above_threshold if row.time_valid]

        state = LinkageState.UNRESOLVED
        rationale: list[str] = []
        matched_market_id = ""
        matched_event_id = ""
        matched_market_title = ""
        matched_event_title = ""
        decision_timestamp_utc: datetime | None = None
        confidence = 0.0
        ambiguity_score = 0.0
        matched_aliases: tuple[str, ...] = ()

        best = time_valid_candidates[0] if time_valid_candidates else None
        second = time_valid_candidates[1] if len(time_valid_candidates) > 1 else None
        if best is not None:
            confidence = best.final_score
            matched_aliases = best.matched_aliases
            if second is not None:
                ambiguity_score = max(0.0, 1.0 - (best.final_score - second.final_score))
                if abs(best.final_score - second.final_score) <= self.ambiguity_margin:
                    state = LinkageState.AMBIGUOUS
                    rationale.append(
                        "multiple markets within ambiguity margin "
                        f"({best.final_score:.4f} vs {second.final_score:.4f})"
                    )
                else:
                    state = LinkageState.LINKED
            else:
                state = LinkageState.LINKED
            if state == LinkageState.LINKED:
                matched_market_id = best.market_id
                matched_event_id = best.event_id
                matched_market_title = best.market_title
                matched_event_title = best.event_title
                decision_timestamp_utc = best.decision_timestamp_utc
                rationale.append("best deterministic similarity score above threshold")
            else:
                rationale.append("explicit uncertain state retained; no forced linkage")
        elif above_threshold and all(not row.time_valid for row in above_threshold):
            state = LinkageState.STALE_EVIDENCE
            confidence = above_threshold[0].final_score if above_threshold else 0.0
            rationale.append("text similarity exists but no candidate passes time-window constraints")
        else:
            state = LinkageState.UNRESOLVED
            if not above_threshold:
                rationale.append("no market/event candidate reached similarity threshold")
            else:
                rationale.append("candidate set unresolved")

        top_candidates = tuple(scored_candidates[: self.max_candidates_per_evidence])
        if not top_candidates:
            rationale.append("no candidates scored")
        return _LinkageDecision(
            result=LinkageResult(
                linkage_id=linkage_id,
                dataset_id=dataset_id,
                source_class=record.source_class,
                source_name=record.source_name,
                evidence_key=record.evidence_key,
                source_record_id=record.source_record_id,
                dedup_key=record.dedup_key,
                state=state,
                confidence=confidence,
                ambiguity_score=ambiguity_score,
                matched_market_id=matched_market_id,
                matched_event_id=matched_event_id,
                matched_market_title=matched_market_title,
                matched_event_title=matched_event_title,
                decision_timestamp_utc=decision_timestamp_utc,
                evidence_title=record.title,
                evidence_url=record.url,
                published_at_utc=record.published_at_utc,
                fetched_at_utc=record.fetched_at_utc,
                extracted_entities=extracted_entities,
                matched_aliases=matched_aliases,
                top_candidates=top_candidates,
                rationale=tuple(rationale),
                created_at_utc=datetime.now(UTC),
            ),
            candidates_for_raw=top_candidates,
        )

    def _score_candidate(
        self,
        *,
        record: EvidenceRecord,
        market: MarketReference,
        evidence_tokens: set[str],
        evidence_title: str,
    ) -> LinkageCandidateScore:
        market_tokens = self._tokenize(market.market_title)
        event_tokens = self._tokenize(market.event_title)
        market_event_tokens = market_tokens | event_tokens

        token_overlap = self._jaccard(evidence_tokens, market_event_tokens)
        title_similarity = self._sequence_similarity(evidence_title, market.market_title)
        event_similarity = self._sequence_similarity(evidence_title, market.event_title)

        alias_matches = self._resolve_alias_matches(
            evidence_tokens=evidence_tokens,
            market_tokens=market_tokens,
            event_tokens=event_tokens,
        )
        alias_overlap = 1.0 if alias_matches else 0.0

        reason_codes: list[str] = []
        published_at = record.published_at_utc
        time_delta_hours: float | None = None
        time_valid = True
        recency_score = 0.5
        if published_at is None:
            time_valid = False
            recency_score = 0.0
            reason_codes.append("missing_published_at")
        else:
            delta_sec = (market.decision_timestamp_utc - published_at).total_seconds()
            time_delta_hours = round(delta_sec / 3600.0, 3)
            if delta_sec < 0:
                time_valid = False
                recency_score = 0.0
                reason_codes.append("post_decision")
            else:
                delta_days = delta_sec / 86400.0
                if self.max_staleness_days > 0 and delta_days > self.max_staleness_days:
                    time_valid = False
                    recency_score = 0.0
                    reason_codes.append("stale_window")
                elif self.max_staleness_days > 0:
                    recency_score = max(0.0, 1.0 - (delta_days / float(self.max_staleness_days)))
                else:
                    recency_score = 1.0

        final_score = (
            (0.40 * token_overlap)
            + (0.25 * title_similarity)
            + (0.20 * event_similarity)
            + (0.10 * alias_overlap)
            + (0.05 * recency_score)
        )
        final_score = max(0.0, min(final_score, 1.0))
        if final_score < self.similarity_threshold:
            reason_codes.append("below_similarity_threshold")
        return LinkageCandidateScore(
            market_id=market.market_id,
            event_id=market.event_id,
            market_title=market.market_title,
            event_title=market.event_title,
            decision_timestamp_utc=market.decision_timestamp_utc,
            title_similarity=title_similarity,
            event_similarity=event_similarity,
            token_overlap=token_overlap,
            alias_overlap=alias_overlap,
            recency_score=recency_score,
            final_score=final_score,
            time_valid=time_valid,
            time_delta_hours=time_delta_hours,
            reason_codes=tuple(reason_codes),
            matched_aliases=tuple(sorted(alias_matches)),
        )

    def _load_markets(
        self,
        *,
        dataset_id: str,
        decision_date_from: date | None,
        decision_date_to: date | None,
        warnings: list[str],
    ) -> list[MarketReference]:
        normalized_dir = self.historical_base_dir / dataset_id / "normalized"
        markets_path = normalized_dir / "markets.jsonl"
        events_path = normalized_dir / "events.jsonl"
        if not markets_path.exists():
            raise FileNotFoundError(
                "Historical normalized markets dataset not found: "
                f"{markets_path}. Run backfill-historical-markets first."
            )
        event_titles = self._load_event_titles(events_path, warnings=warnings)
        markets: list[MarketReference] = []
        with markets_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    warnings.append(f"ignored invalid JSON market row line={line_number}")
                    continue
                if not isinstance(row, dict):
                    warnings.append(f"ignored non-object market row line={line_number}")
                    continue
                market_id = str(row.get("market_id") or "").strip()
                if not market_id:
                    warnings.append(f"ignored market row without market_id line={line_number}")
                    continue
                event_id = str(row.get("event_id") or "").strip()
                decision_ts = parse_datetime_utc(row.get("close_at_utc")) or parse_datetime_utc(row.get("resolved_at_utc"))
                if decision_ts is None:
                    warnings.append(f"ignored market row without decision timestamp market_id={market_id}")
                    continue
                if decision_date_from is not None and decision_ts.date() < decision_date_from:
                    continue
                if decision_date_to is not None and decision_ts.date() > decision_date_to:
                    continue
                markets.append(
                    MarketReference(
                        market_id=market_id,
                        event_id=event_id,
                        market_title=str(row.get("question") or "").strip(),
                        event_title=event_titles.get(event_id, ""),
                        category=str(row.get("category") or "").strip().lower(),
                        decision_timestamp_utc=decision_ts,
                    )
                )
        return sorted(markets, key=lambda row: (row.decision_timestamp_utc, row.market_id))

    @staticmethod
    def _load_event_titles(path: Path, *, warnings: list[str]) -> dict[str, str]:
        if not path.exists():
            warnings.append(f"events dataset missing at {path}; event-title similarity fallback disabled")
            return {}
        rows: dict[str, str] = {}
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    warnings.append(f"ignored invalid JSON event row line={line_number}")
                    continue
                if not isinstance(row, dict):
                    continue
                event_id = str(row.get("event_id") or "").strip()
                title = str(row.get("title") or "").strip()
                if event_id and title:
                    rows[event_id] = title
        return rows

    def _load_evidence(
        self,
        *,
        news_corpus_id: str,
        reddit_corpus_id: str,
        x_corpus_id: str,
        warnings: list[str],
    ) -> list[EvidenceRecord]:
        rows: list[EvidenceRecord] = []
        if "news_rss_web" in self.enabled_source_classes:
            rows.extend(self._load_news_evidence(corpus_id=news_corpus_id, warnings=warnings))
        if "reddit" in self.enabled_source_classes:
            rows.extend(self._load_reddit_evidence(corpus_id=reddit_corpus_id, warnings=warnings))
        if "x" in self.enabled_source_classes:
            rows.extend(self._load_x_evidence(corpus_id=x_corpus_id, warnings=warnings))
        deduped: dict[str, EvidenceRecord] = {}
        for row in rows:
            deduped.setdefault(row.evidence_key, row)
        ordered = sorted(
            deduped.values(),
            key=lambda row: (
                row.published_at_utc or datetime(1970, 1, 1, tzinfo=UTC),
                row.evidence_key,
            ),
        )
        return ordered

    def _load_news_evidence(self, *, corpus_id: str, warnings: list[str]) -> list[EvidenceRecord]:
        path = self.news_corpus_base_dir / corpus_id / "normalized" / "news_articles.jsonl"
        if not path.exists():
            warnings.append(f"news corpus not found at {path}")
            return []
        rows: list[EvidenceRecord] = []
        for line_number, row in enumerate(self._read_jsonl(path), start=1):
            source_class = str(row.get("source_class") or "news_rss_web").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                warnings.append(f"news row missing source_record_id line={line_number}")
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            rows.append(
                EvidenceRecord(
                    evidence_key=evidence_key,
                    source_class=source_class,
                    source_name=str(row.get("source_name") or "").strip(),
                    source_record_id=source_record_id,
                    dedup_key=dedup_key,
                    title=str(row.get("title") or "").strip(),
                    body=str(row.get("summary") or "").strip(),
                    url=str(row.get("article_url") or "").strip(),
                    published_at_utc=parse_datetime_utc(row.get("published_at_utc")),
                    fetched_at_utc=parse_datetime_utc(row.get("fetched_at_utc")),
                    metadata={
                        "query": str(row.get("query") or "").strip(),
                        "query_kind": str(row.get("query_kind") or "").strip(),
                        "article_domain": str(row.get("article_domain") or "").strip(),
                        "publisher": str(row.get("publisher") or "").strip(),
                    },
                )
            )
        return rows

    def _load_reddit_evidence(self, *, corpus_id: str, warnings: list[str]) -> list[EvidenceRecord]:
        path = self.reddit_corpus_base_dir / corpus_id / "normalized" / "reddit_evidence.jsonl"
        if not path.exists():
            warnings.append(f"reddit corpus not found at {path}")
            return []
        rows: list[EvidenceRecord] = []
        for line_number, row in enumerate(self._read_jsonl(path), start=1):
            source_class = str(row.get("source_class") or "reddit").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                warnings.append(f"reddit row missing source_record_id line={line_number}")
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            rows.append(
                EvidenceRecord(
                    evidence_key=evidence_key,
                    source_class=source_class,
                    source_name=str(row.get("source_name") or "").strip(),
                    source_record_id=source_record_id,
                    dedup_key=dedup_key,
                    title=str(row.get("title") or "").strip(),
                    body=str(row.get("body") or "").strip(),
                    url=str(row.get("permalink_url") or row.get("external_url") or "").strip(),
                    published_at_utc=parse_datetime_utc(row.get("created_at_utc")),
                    fetched_at_utc=parse_datetime_utc(row.get("fetched_at_utc")),
                    metadata={
                        "query": str(row.get("query") or "").strip(),
                        "query_kind": str(row.get("query_kind") or "").strip(),
                        "evidence_kind": str(row.get("evidence_kind") or "").strip(),
                        "subreddit": str(row.get("subreddit") or "").strip(),
                        "author": str(row.get("author") or "").strip(),
                    },
                )
            )
        return rows

    def _load_x_evidence(self, *, corpus_id: str, warnings: list[str]) -> list[EvidenceRecord]:
        path = self.x_corpus_base_dir / corpus_id / "normalized" / "x_evidence.jsonl"
        if not path.exists():
            warnings.append(f"x corpus not found at {path}")
            return []
        rows: list[EvidenceRecord] = []
        for line_number, row in enumerate(self._read_jsonl(path), start=1):
            source_class = str(row.get("source_class") or "x").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                warnings.append(f"x row missing source_record_id line={line_number}")
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            rows.append(
                EvidenceRecord(
                    evidence_key=evidence_key,
                    source_class=source_class,
                    source_name=str(row.get("source_name") or "").strip(),
                    source_record_id=source_record_id,
                    dedup_key=dedup_key,
                    title=str(row.get("text") or "").strip(),
                    body="",
                    url=str(row.get("post_url") or "").strip(),
                    published_at_utc=parse_datetime_utc(row.get("created_at_utc")),
                    fetched_at_utc=parse_datetime_utc(row.get("fetched_at_utc")),
                    metadata={
                        "query": str(row.get("query") or "").strip(),
                        "query_kind": str(row.get("query_kind") or "").strip(),
                        "author_username": str(row.get("author_username") or "").strip(),
                        "lang": str(row.get("lang") or "").strip(),
                    },
                )
            )
        return rows

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
    def _tokenize(text: str) -> set[str]:
        tokens: set[str] = set()
        for match in _TOKEN_PATTERN.findall(text.lower()):
            token = match.strip("'").strip("-")
            if len(token) < 2 or token in _STOPWORDS:
                continue
            tokens.add(token)
        return tokens

    def _extract_entities(self, text: str) -> list[str]:
        tokens = [token for token in _TOKEN_PATTERN.findall(text.lower()) if token not in _STOPWORDS and len(token) >= 3]
        entities: list[str] = []
        entities.extend(tokens)
        for first, second in zip(tokens, tokens[1:]):
            entities.append(f"{first} {second}")
        deduped = sorted(set(entities))
        return deduped[:24]

    @staticmethod
    def _jaccard(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        intersection = len(left & right)
        union = len(left | right)
        if union == 0:
            return 0.0
        return intersection / union

    @staticmethod
    def _sequence_similarity(left: str, right: str) -> float:
        left_text = left.strip().lower()
        right_text = right.strip().lower()
        if not left_text or not right_text:
            return 0.0
        return SequenceMatcher(None, left_text, right_text).ratio()

    def _resolve_alias_matches(
        self,
        *,
        evidence_tokens: set[str],
        market_tokens: set[str],
        event_tokens: set[str],
    ) -> set[str]:
        matched: set[str] = set()
        market_event_tokens = market_tokens | event_tokens
        for alias_tokens, canonical_tokens, canonical_label in self.alias_lookup:
            alias_present_in_evidence = alias_tokens <= evidence_tokens
            alias_present_in_market = alias_tokens <= market_event_tokens
            canonical_in_evidence = canonical_tokens <= evidence_tokens
            canonical_in_market = canonical_tokens <= market_event_tokens
            if (alias_present_in_evidence and canonical_in_market) or (canonical_in_evidence and alias_present_in_market):
                matched.add(canonical_label)
        return matched

    @staticmethod
    def _normalize_alias_map(alias_map: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
        normalized: dict[str, tuple[str, ...]] = {}
        for canonical, raw_aliases in alias_map.items():
            canonical_text = str(canonical).strip().lower()
            if not canonical_text:
                continue
            aliases: list[str] = []
            for alias in raw_aliases:
                alias_text = str(alias).strip().lower()
                if alias_text:
                    aliases.append(alias_text)
            if aliases:
                normalized[canonical_text] = tuple(sorted(set(aliases)))
        return normalized

    def _build_alias_lookup(
        self,
        alias_map: Mapping[str, Sequence[str]],
    ) -> tuple[tuple[set[str], set[str], str], ...]:
        rows: list[tuple[set[str], set[str], str]] = []
        for canonical, aliases in alias_map.items():
            canonical_tokens = self._tokenize(canonical)
            if not canonical_tokens:
                continue
            for alias in aliases:
                alias_tokens = self._tokenize(alias)
                if alias_tokens:
                    rows.append((alias_tokens, canonical_tokens, canonical))
        return tuple(rows)

    @staticmethod
    def _load_existing_result_keys(storage: LinkageStorage) -> set[str]:
        keys: set[str] = set()
        for row in storage.read_rows(normalized=True, name="linkage_results"):
            evidence_key = str(row.get("evidence_key") or "").strip()
            if evidence_key:
                keys.add(evidence_key)
        return keys

    @staticmethod
    def _load_existing_candidate_keys(storage: LinkageStorage) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for row in storage.read_rows(normalized=False, name="linkage_candidates"):
            evidence_key = str(row.get("evidence_key") or "").strip()
            candidate = row.get("candidate")
            if not isinstance(candidate, dict):
                continue
            market_id = str(candidate.get("market_id") or "").strip()
            if evidence_key and market_id:
                keys.add((evidence_key, market_id))
        return keys

    @staticmethod
    def _state_counts(storage: LinkageStorage) -> dict[str, int]:
        counts = {state.value: 0 for state in LinkageState}
        for row in storage.read_rows(normalized=True, name="linkage_results"):
            state = str(row.get("state") or "").strip().lower()
            if state in counts:
                counts[state] += 1
        return counts
