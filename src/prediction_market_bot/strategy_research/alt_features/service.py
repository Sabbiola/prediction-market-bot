from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import (
    AltFeatureBuildSummary,
    AltFeatureColumn,
    AltFeatureParityVerification,
    AltFeatureSchemaInspection,
)
from .schema import ALT_FEATURE_SCHEMA, ALT_FEATURE_SCHEMA_VERSION
from .storage import AltFeatureDatasetLayout, AltFeatureDatasetStorage

_DEFAULT_SOURCE_PRIORS: dict[str, float] = {
    "news_rss_web": 0.72,
    "reddit": 0.52,
    "x": 0.45,
}

_CATALYST_STRENGTH: dict[str, float] = {
    "policy": 0.80,
    "regulation": 0.82,
    "legal": 0.78,
    "macro": 0.70,
    "earnings": 0.60,
    "geopolitics": 0.85,
    "technology": 0.55,
    "other": 0.40,
}


def _clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


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
class _SourcePayload:
    source_name: str
    source_identity: str
    published_at_utc: datetime | None
    attention_score: float


@dataclass(slots=True, frozen=True)
class _EvidenceRecord:
    source_class: str
    source_name: str
    source_identity: str
    linkage_state: str
    published_at_utc: datetime
    decision_timestamp_utc: datetime
    age_hours: float
    source_credibility_prior: float
    attention_score: float
    has_enrichment: bool
    enrichment_relevance: float
    contradiction_score: float
    novelty_score: float
    catalyst_strength: float


@dataclass(slots=True, frozen=True)
class _EvidenceBuildCounters:
    accepted_rows: int = 0
    skipped_missing_published_at: int = 0
    skipped_post_decision_evidence: int = 0


class AltFeatureDatasetBuilderService:
    def __init__(
        self,
        *,
        historical_base_dir: Path,
        linkage_base_dir: Path,
        news_corpus_base_dir: Path,
        reddit_corpus_base_dir: Path,
        x_corpus_base_dir: Path,
        llm_enrichment_base_dir: Path,
        default_dataset_id: str,
        default_linkage_id: str,
        default_news_corpus_id: str,
        default_reddit_corpus_id: str,
        default_x_corpus_id: str,
        default_enrichment_id: str,
        schema_version: str = ALT_FEATURE_SCHEMA_VERSION,
    ) -> None:
        self.historical_base_dir = historical_base_dir
        self.linkage_base_dir = linkage_base_dir
        self.news_corpus_base_dir = news_corpus_base_dir
        self.reddit_corpus_base_dir = reddit_corpus_base_dir
        self.x_corpus_base_dir = x_corpus_base_dir
        self.llm_enrichment_base_dir = llm_enrichment_base_dir
        self.default_dataset_id = default_dataset_id
        self.default_linkage_id = default_linkage_id
        self.default_news_corpus_id = default_news_corpus_id
        self.default_reddit_corpus_id = default_reddit_corpus_id
        self.default_x_corpus_id = default_x_corpus_id
        self.default_enrichment_id = default_enrichment_id
        self.schema_version = schema_version

    def build_alt_feature_dataset(
        self,
        *,
        dataset_id: str | None = None,
        linkage_id: str | None = None,
        news_corpus_id: str | None = None,
        reddit_corpus_id: str | None = None,
        x_corpus_id: str | None = None,
        enrichment_id: str | None = None,
    ) -> AltFeatureBuildSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        effective_linkage_id = (linkage_id or self.default_linkage_id).strip() or self.default_linkage_id
        effective_news_corpus_id = (news_corpus_id or self.default_news_corpus_id).strip() or self.default_news_corpus_id
        effective_reddit_corpus_id = (
            (reddit_corpus_id or self.default_reddit_corpus_id).strip() or self.default_reddit_corpus_id
        )
        effective_x_corpus_id = (x_corpus_id or self.default_x_corpus_id).strip() or self.default_x_corpus_id
        effective_enrichment_id = (enrichment_id or self.default_enrichment_id).strip() or self.default_enrichment_id

        layout = AltFeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
        )
        storage = AltFeatureDatasetStorage(layout)

        normalized_root = layout.dataset_root / "normalized"
        markets_path = normalized_root / "markets.jsonl"
        if not markets_path.exists():
            raise FileNotFoundError(f"Historical dataset missing markets file: {markets_path}")
        market_rows = self._read_jsonl(markets_path)
        resolution_rows = self._read_jsonl(normalized_root / "resolutions.jsonl")
        resolution_by_market = self._resolution_by_market(resolution_rows)

        evidence_by_key, evidence_counters = self._evidence_by_market_and_decision(
            linkage_id=effective_linkage_id,
            news_corpus_id=effective_news_corpus_id,
            reddit_corpus_id=effective_reddit_corpus_id,
            x_corpus_id=effective_x_corpus_id,
            enrichment_id=effective_enrichment_id,
        )

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_row_ids: set[str] = set()
        skipped_missing_decision = 0
        skipped_unresolved = 0
        duplicate_rows = 0

        for market_row in market_rows:
            market_id = str(market_row.get("market_id") or "").strip()
            if not market_id:
                continue
            decision_ts = self._resolve_decision_timestamp(market_row)
            if decision_ts is None:
                skipped_missing_decision += 1
                continue
            outcome = self._resolve_outcome(market_row, resolution_by_market.get(market_id))
            if outcome not in {"YES", "NO"}:
                skipped_unresolved += 1
                continue
            label_yes = 1 if outcome == "YES" else 0
            decision_iso = decision_ts.isoformat()
            row_id = f"{market_id}@{decision_iso}"
            if row_id in seen_row_ids:
                duplicate_rows += 1
                continue
            seen_row_ids.add(row_id)
            flags: set[str] = set()

            event_id = str(market_row.get("event_id") or "").strip()
            category = str(market_row.get("category") or "").strip() or "unknown"
            evidence_rows = evidence_by_key.get((market_id, decision_iso), ())
            alt_features = self._compute_alt_features(evidence_rows)

            if int(alt_features["f_alt_evidence_count"]) == 0:
                flags.add("missing_alt_evidence")
            if float(alt_features["f_alt_enrichment_coverage"]) == 0.0:
                flags.add("missing_llm_enrichment")

            rows.append(
                {
                    "row_id": row_id,
                    "feature_schema_version": self.schema_version,
                    "market_id": market_id,
                    "event_id": event_id,
                    "category": category,
                    "decision_timestamp_utc": decision_iso,
                    "label_yes": label_yes,
                    "data_quality_flags": sorted(flags),
                    **alt_features,
                }
            )

        rows.sort(key=lambda row: (str(row.get("decision_timestamp_utc", "")), str(row.get("market_id", ""))))
        rows_path = storage.write_rows(rows)
        schema_path = storage.write_schema(
            {
                **ALT_FEATURE_SCHEMA.to_dict(),
                "generated_at_utc": datetime.now(UTC).isoformat(),
            }
        )
        manifest_path = storage.write_manifest(
            {
                "dataset_id": effective_dataset_id,
                "linkage_id": effective_linkage_id,
                "news_corpus_id": effective_news_corpus_id,
                "reddit_corpus_id": effective_reddit_corpus_id,
                "x_corpus_id": effective_x_corpus_id,
                "enrichment_id": effective_enrichment_id,
                "schema_version": self.schema_version,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "rows_path": str(rows_path),
                "schema_path": str(schema_path),
                "counts": {
                    "market_rows_total": len(market_rows),
                    "rows_written": len(rows),
                    "skipped_missing_decision_timestamp": skipped_missing_decision,
                    "skipped_unresolved_or_ambiguous": skipped_unresolved,
                    "duplicate_rows": duplicate_rows,
                    "accepted_evidence_rows": evidence_counters.accepted_rows,
                    "skipped_missing_published_at": evidence_counters.skipped_missing_published_at,
                    "skipped_post_decision_evidence": evidence_counters.skipped_post_decision_evidence,
                },
                "warnings": warnings,
            }
        )
        return AltFeatureBuildSummary(
            dataset_id=effective_dataset_id,
            linkage_id=effective_linkage_id,
            enrichment_id=effective_enrichment_id,
            schema_version=self.schema_version,
            rows_path=rows_path,
            schema_path=schema_path,
            manifest_path=manifest_path,
            rows_written=len(rows),
            skipped_missing_decision_timestamp=skipped_missing_decision,
            skipped_unresolved_or_ambiguous=skipped_unresolved,
            skipped_missing_published_at=evidence_counters.skipped_missing_published_at,
            skipped_post_decision_evidence=evidence_counters.skipped_post_decision_evidence,
            duplicate_rows=duplicate_rows,
            warnings=tuple(warnings),
        )

    def inspect_alt_feature_schema(self, *, dataset_id: str | None = None) -> AltFeatureSchemaInspection:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = AltFeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
        )
        storage = AltFeatureDatasetStorage(layout)
        schema_payload = storage.read_schema()
        columns = self._columns_from_schema_payload(schema_payload)
        if not columns:
            columns = ALT_FEATURE_SCHEMA.columns
        return AltFeatureSchemaInspection(
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
            schema_path=layout.schema_path,
            rows_path=layout.rows_path,
            manifest_path=layout.manifest_path,
            schema_exists=layout.schema_path.exists(),
            rows_exist=layout.rows_path.exists(),
            rows_count=storage.row_count(),
            columns=columns,
        )

    def verify_alt_feature_parity(self, *, dataset_id: str | None = None) -> AltFeatureParityVerification:
        inspection = self.inspect_alt_feature_schema(dataset_id=dataset_id)
        layout = AltFeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=inspection.dataset_id,
            schema_version=self.schema_version,
        )
        storage = AltFeatureDatasetStorage(layout)
        errors: list[str] = []
        warnings: list[str] = []
        expected_columns = {column.name for column in inspection.columns}
        if not inspection.schema_exists:
            errors.append(f"alt feature schema file missing: {inspection.schema_path}")
        rows = storage.read_rows()
        if not rows:
            warnings.append("alt_feature_rows.jsonl is empty")
        missing_columns = 0
        extra_columns = 0
        version_mismatch = 0
        invalid_decision_timestamp = 0
        invalid_label = 0
        for row in rows:
            keys = set(row.keys())
            row_missing = expected_columns - keys
            row_extra = keys - expected_columns
            if row_missing:
                missing_columns += len(row_missing)
            if row_extra:
                extra_columns += len(row_extra)
            if str(row.get("feature_schema_version") or "").strip() != self.schema_version:
                version_mismatch += 1
            if parse_datetime_utc(row.get("decision_timestamp_utc")) is None:
                invalid_decision_timestamp += 1
            if row.get("label_yes") not in {0, 1}:
                invalid_label += 1

        if missing_columns > 0:
            errors.append(f"rows_missing_columns_total={missing_columns}")
        if extra_columns > 0:
            errors.append(f"rows_extra_columns_total={extra_columns}")
        if version_mismatch > 0:
            errors.append(f"rows_with_schema_version_mismatch={version_mismatch}")
        if invalid_decision_timestamp > 0:
            errors.append(f"rows_with_invalid_decision_timestamp={invalid_decision_timestamp}")
        if invalid_label > 0:
            errors.append(f"rows_with_invalid_label_yes={invalid_label}")
        details = {
            "rows_path": str(layout.rows_path),
            "schema_path": str(layout.schema_path),
            "manifest_path": str(layout.manifest_path),
            "rows_count": len(rows),
            "expected_column_count": len(expected_columns),
            "missing_columns_total": missing_columns,
            "extra_columns_total": extra_columns,
            "schema_version_mismatch_rows": version_mismatch,
            "invalid_decision_timestamp_rows": invalid_decision_timestamp,
            "invalid_label_rows": invalid_label,
        }
        return AltFeatureParityVerification(
            dataset_id=inspection.dataset_id,
            schema_version=self.schema_version,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _evidence_by_market_and_decision(
        self,
        *,
        linkage_id: str,
        news_corpus_id: str,
        reddit_corpus_id: str,
        x_corpus_id: str,
        enrichment_id: str,
    ) -> tuple[dict[tuple[str, str], tuple[_EvidenceRecord, ...]], _EvidenceBuildCounters]:
        linkage_path = self.linkage_base_dir / linkage_id / "normalized" / "linkage_results.jsonl"
        if not linkage_path.exists():
            raise FileNotFoundError(
                f"Linkage dataset missing linkage_results file: {linkage_path}. Run build-linkage first."
            )

        news_map = self._load_news_records(corpus_id=news_corpus_id)
        reddit_map = self._load_reddit_records(corpus_id=reddit_corpus_id)
        x_map = self._load_x_records(corpus_id=x_corpus_id)
        enrichment_map = self._load_enrichment_records(enrichment_id=enrichment_id)

        grouped: dict[tuple[str, str], list[_EvidenceRecord]] = {}
        counters = _EvidenceBuildCounters()
        for row in self._read_jsonl(linkage_path):
            market_id = str(row.get("matched_market_id") or "").strip()
            decision_ts = parse_datetime_utc(row.get("decision_timestamp_utc"))
            source_class = str(row.get("source_class") or "").strip().lower()
            linkage_state = str(row.get("state") or "").strip().lower()
            if not market_id or decision_ts is None or source_class not in {"news_rss_web", "reddit", "x"}:
                continue
            if linkage_state not in {"linked", "ambiguous"}:
                continue

            source_record_id = str(row.get("source_record_id") or "").strip()
            dedup_key = str(row.get("dedup_key") or "").strip()
            evidence_key = str(row.get("evidence_key") or "").strip()
            if not evidence_key:
                evidence_key = f"{source_class}:{dedup_key or source_record_id}"

            payload = self._resolve_source_payload(
                source_class=source_class,
                evidence_key=evidence_key,
                source_record_id=source_record_id,
                dedup_key=dedup_key,
                news_map=news_map,
                reddit_map=reddit_map,
                x_map=x_map,
            )
            published_at = parse_datetime_utc(row.get("published_at_utc"))
            if published_at is None and payload is not None:
                published_at = payload.published_at_utc
            if published_at is None:
                counters = _EvidenceBuildCounters(
                    accepted_rows=counters.accepted_rows,
                    skipped_missing_published_at=counters.skipped_missing_published_at + 1,
                    skipped_post_decision_evidence=counters.skipped_post_decision_evidence,
                )
                continue
            if published_at > decision_ts:
                counters = _EvidenceBuildCounters(
                    accepted_rows=counters.accepted_rows,
                    skipped_missing_published_at=counters.skipped_missing_published_at,
                    skipped_post_decision_evidence=counters.skipped_post_decision_evidence + 1,
                )
                continue

            source_name = str(row.get("source_name") or "").strip()
            source_identity = source_name or source_class
            attention_score = 0.0
            if payload is not None:
                source_name = payload.source_name or source_name
                source_identity = payload.source_identity or source_identity
                attention_score = payload.attention_score

            enrichment = enrichment_map.get(evidence_key)
            has_enrichment = enrichment is not None
            relevance = 0.5
            contradiction_score = 0.0
            novelty_score = 0.0
            catalyst_strength = 0.0
            if enrichment is not None:
                relevance = _clamp(self._to_float(enrichment.get("relevance_score")) or 0.5, lower=0.0, upper=1.0)
                contradiction_score = _clamp(
                    self._to_float(enrichment.get("contradiction_score")) or 0.0,
                    lower=0.0,
                    upper=1.0,
                )
                novelty_score = _clamp(self._to_float(enrichment.get("novelty_score")) or 0.0, lower=0.0, upper=1.0)
                catalyst_class = str(enrichment.get("catalyst_class") or "other").strip().lower()
                catalyst_strength = _CATALYST_STRENGTH.get(catalyst_class, _CATALYST_STRENGTH["other"])

            age_hours = max((decision_ts - published_at).total_seconds() / 3600.0, 0.0)
            prior = self._source_prior(source_class=source_class, source_name=source_name)
            grouped.setdefault((market_id, decision_ts.isoformat()), []).append(
                _EvidenceRecord(
                    source_class=source_class,
                    source_name=source_name,
                    source_identity=source_identity,
                    linkage_state=linkage_state,
                    published_at_utc=published_at,
                    decision_timestamp_utc=decision_ts,
                    age_hours=age_hours,
                    source_credibility_prior=prior,
                    attention_score=max(attention_score, 0.0),
                    has_enrichment=has_enrichment,
                    enrichment_relevance=relevance,
                    contradiction_score=contradiction_score,
                    novelty_score=novelty_score,
                    catalyst_strength=catalyst_strength,
                )
            )
            counters = _EvidenceBuildCounters(
                accepted_rows=counters.accepted_rows + 1,
                skipped_missing_published_at=counters.skipped_missing_published_at,
                skipped_post_decision_evidence=counters.skipped_post_decision_evidence,
            )
        return {key: tuple(value) for key, value in grouped.items()}, counters

    @staticmethod
    def _compute_alt_features(evidence_rows: Sequence[_EvidenceRecord]) -> dict[str, int | float]:
        if not evidence_rows:
            return {
                "f_alt_evidence_count": 0,
                "f_alt_linked_evidence_count": 0,
                "f_alt_market_linked_coverage": 0.0,
                "f_alt_source_diversity": 0.0,
                "f_alt_source_credibility_prior": 0.0,
                "f_alt_freshness_decay": 0.0,
                "f_alt_news_volume_24h": 0,
                "f_alt_news_burstiness_24h": 0.0,
                "f_alt_reddit_mentions_24h": 0,
                "f_alt_reddit_attention": 0.0,
                "f_alt_x_mentions_24h": 0,
                "f_alt_x_attention": 0.0,
                "f_alt_contradiction_score": 0.0,
                "f_alt_novelty_score": 0.0,
                "f_alt_catalyst_strength_score": 0.0,
                "f_alt_enrichment_coverage": 0.0,
            }

        total_count = len(evidence_rows)
        linked_count = sum(1 for row in evidence_rows if row.linkage_state == "linked")
        market_linked_coverage = linked_count / total_count if total_count > 0 else 0.0

        source_counts: dict[str, int] = {}
        for row in evidence_rows:
            identity = row.source_identity.strip().lower() or row.source_class
            source_counts[identity] = source_counts.get(identity, 0) + 1
        unique_sources = len(source_counts)
        if unique_sources <= 1:
            source_diversity = 0.0
        else:
            simpson = 1.0 - sum((count / total_count) ** 2 for count in source_counts.values())
            max_simpson = 1.0 - (1.0 / unique_sources)
            source_diversity = _clamp(simpson / max(max_simpson, 1e-9), lower=0.0, upper=1.0)

        source_prior = sum(row.source_credibility_prior for row in evidence_rows) / total_count
        freshness_weights = [max(row.source_credibility_prior, 0.01) for row in evidence_rows]
        freshness_weight_total = sum(freshness_weights)
        freshness_decay = 0.0
        for row, weight in zip(evidence_rows, freshness_weights):
            decay = math.exp(-math.log(2.0) * (row.age_hours / 24.0))
            freshness_decay += decay * weight
        freshness_decay = freshness_decay / freshness_weight_total if freshness_weight_total > 0 else 0.0

        news_recent = sum(1 for row in evidence_rows if row.source_class == "news_rss_web" and row.age_hours <= 24.0)
        news_prev = sum(
            1 for row in evidence_rows if row.source_class == "news_rss_web" and 24.0 < row.age_hours <= 48.0
        )
        news_burstiness = _clamp(((news_recent + 1.0) / (news_prev + 1.0)) - 1.0, lower=-1.0, upper=10.0)

        reddit_rows = [row for row in evidence_rows if row.source_class == "reddit" and row.age_hours <= 24.0]
        reddit_mentions = len(reddit_rows)
        reddit_attention = math.log1p(sum(max(row.attention_score, 0.0) for row in reddit_rows))

        x_rows = [row for row in evidence_rows if row.source_class == "x" and row.age_hours <= 24.0]
        x_mentions = len(x_rows)
        x_attention = math.log1p(sum(max(row.attention_score, 0.0) for row in x_rows))

        enriched_rows = [row for row in evidence_rows if row.has_enrichment]
        enrichment_coverage = len(enriched_rows) / total_count if total_count > 0 else 0.0
        contradiction = 0.0
        novelty = 0.0
        catalyst = 0.0
        if enriched_rows:
            weights = [max(row.enrichment_relevance, 0.01) for row in enriched_rows]
            weight_total = sum(weights)
            contradiction = sum(row.contradiction_score * weight for row, weight in zip(enriched_rows, weights)) / weight_total
            novelty = sum(row.novelty_score * weight for row, weight in zip(enriched_rows, weights)) / weight_total
            catalyst = sum(row.catalyst_strength * weight for row, weight in zip(enriched_rows, weights)) / weight_total

        return {
            "f_alt_evidence_count": int(total_count),
            "f_alt_linked_evidence_count": int(linked_count),
            "f_alt_market_linked_coverage": round(_clamp(market_linked_coverage, lower=0.0, upper=1.0), 8),
            "f_alt_source_diversity": round(_clamp(source_diversity, lower=0.0, upper=1.0), 8),
            "f_alt_source_credibility_prior": round(_clamp(source_prior, lower=0.0, upper=1.0), 8),
            "f_alt_freshness_decay": round(_clamp(freshness_decay, lower=0.0, upper=1.0), 8),
            "f_alt_news_volume_24h": int(news_recent),
            "f_alt_news_burstiness_24h": round(news_burstiness, 8),
            "f_alt_reddit_mentions_24h": int(reddit_mentions),
            "f_alt_reddit_attention": round(max(reddit_attention, 0.0), 8),
            "f_alt_x_mentions_24h": int(x_mentions),
            "f_alt_x_attention": round(max(x_attention, 0.0), 8),
            "f_alt_contradiction_score": round(_clamp(contradiction, lower=0.0, upper=1.0), 8),
            "f_alt_novelty_score": round(_clamp(novelty, lower=0.0, upper=1.0), 8),
            "f_alt_catalyst_strength_score": round(_clamp(catalyst, lower=0.0, upper=1.0), 8),
            "f_alt_enrichment_coverage": round(_clamp(enrichment_coverage, lower=0.0, upper=1.0), 8),
        }

    def _load_news_records(self, *, corpus_id: str) -> dict[str, _SourcePayload]:
        path = self.news_corpus_base_dir / corpus_id / "normalized" / "news_articles.jsonl"
        if not path.exists():
            return {}
        records: dict[str, _SourcePayload] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "news_rss_web").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            source_name = str(row.get("source_name") or "").strip() or "news"
            publisher = str(row.get("publisher") or "").strip()
            article_domain = str(row.get("article_domain") or "").strip()
            source_identity = publisher or article_domain or source_name
            records[evidence_key] = _SourcePayload(
                source_name=source_name,
                source_identity=source_identity.strip().lower() or source_name,
                published_at_utc=parse_datetime_utc(row.get("published_at_utc")),
                attention_score=1.0,
            )
        return records

    def _load_reddit_records(self, *, corpus_id: str) -> dict[str, _SourcePayload]:
        path = self.reddit_corpus_base_dir / corpus_id / "normalized" / "reddit_evidence.jsonl"
        if not path.exists():
            return {}
        records: dict[str, _SourcePayload] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "reddit").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            score = max(self._to_float(row.get("score")) or 0.0, 0.0)
            num_comments = max(self._to_float(row.get("num_comments")) or 0.0, 0.0)
            attention = score + (0.5 * num_comments)
            source_name = str(row.get("source_name") or "").strip() or "reddit"
            subreddit = str(row.get("subreddit") or "").strip()
            source_identity = f"reddit:{subreddit.lower()}" if subreddit else source_name
            records[evidence_key] = _SourcePayload(
                source_name=source_name,
                source_identity=source_identity,
                published_at_utc=parse_datetime_utc(row.get("created_at_utc")),
                attention_score=attention,
            )
        return records

    def _load_x_records(self, *, corpus_id: str) -> dict[str, _SourcePayload]:
        path = self.x_corpus_base_dir / corpus_id / "normalized" / "x_evidence.jsonl"
        if not path.exists():
            return {}
        records: dict[str, _SourcePayload] = {}
        for row in self._read_jsonl(path):
            source_class = str(row.get("source_class") or "x").strip().lower()
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or dedup_key).strip()
            if not source_record_id:
                continue
            evidence_key = f"{source_class}:{dedup_key or source_record_id}"
            metrics = row.get("public_metrics")
            metrics_map = metrics if isinstance(metrics, Mapping) else {}
            like_count = max(self._to_float(metrics_map.get("like_count")) or 0.0, 0.0)
            retweet_count = max(self._to_float(metrics_map.get("retweet_count")) or 0.0, 0.0)
            reply_count = max(self._to_float(metrics_map.get("reply_count")) or 0.0, 0.0)
            quote_count = max(self._to_float(metrics_map.get("quote_count")) or 0.0, 0.0)
            attention = like_count + (0.75 * retweet_count) + (0.50 * reply_count) + (0.30 * quote_count)
            source_name = str(row.get("source_name") or "").strip() or "x"
            author_username = str(row.get("author_username") or "").strip()
            author_id = str(row.get("author_id") or "").strip()
            source_identity = (
                f"x:{author_username.lower()}" if author_username else f"x:{author_id}" if author_id else source_name
            )
            records[evidence_key] = _SourcePayload(
                source_name=source_name,
                source_identity=source_identity,
                published_at_utc=parse_datetime_utc(row.get("created_at_utc")),
                attention_score=attention,
            )
        return records

    def _load_enrichment_records(self, *, enrichment_id: str) -> dict[str, Mapping[str, Any]]:
        path = self.llm_enrichment_base_dir / enrichment_id / "normalized" / "enrichment_records.jsonl"
        if not path.exists():
            return {}
        latest_rows: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
        for row in self._read_jsonl(path):
            evidence_key = str(row.get("evidence_key") or "").strip()
            status = str(row.get("status") or "").strip().lower()
            output = row.get("output")
            if not evidence_key or status == "failed" or not isinstance(output, Mapping):
                continue
            created_at = parse_datetime_utc(row.get("created_at_utc")) or datetime(1970, 1, 1, tzinfo=UTC)
            current = latest_rows.get(evidence_key)
            if current is None or created_at >= current[0]:
                latest_rows[evidence_key] = (created_at, output)
        return {key: value[1] for key, value in latest_rows.items()}

    @staticmethod
    def _resolve_source_payload(
        *,
        source_class: str,
        evidence_key: str,
        source_record_id: str,
        dedup_key: str,
        news_map: Mapping[str, _SourcePayload],
        reddit_map: Mapping[str, _SourcePayload],
        x_map: Mapping[str, _SourcePayload],
    ) -> _SourcePayload | None:
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
        return source_map.get(f"{source_class}:{dedup_key or source_record_id}")

    @staticmethod
    def _source_prior(*, source_class: str, source_name: str) -> float:
        normalized = source_name.strip().lower()
        if normalized in {"rss_news_adapter", "google-news", "google-news-rss"}:
            return 0.75
        return _DEFAULT_SOURCE_PRIORS.get(source_class, 0.60)

    @staticmethod
    def _resolve_decision_timestamp(row: Mapping[str, Any]) -> datetime | None:
        for key in ("decision_timestamp_utc", "close_at_utc", "close_at", "market_close_at_utc", "resolved_at_utc"):
            parsed = parse_datetime_utc(row.get(key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _resolve_outcome(market_row: Mapping[str, Any], resolution_row: Mapping[str, Any] | None) -> str:
        market_outcome = str(market_row.get("resolved_outcome") or "").strip().upper()
        if market_outcome in {"YES", "NO"}:
            return market_outcome
        if resolution_row is None:
            return ""
        resolution_outcome = str(resolution_row.get("resolved_outcome") or "").strip().upper()
        if resolution_outcome in {"YES", "NO"}:
            return resolution_outcome
        return ""

    @staticmethod
    def _resolution_by_market(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        payload: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            if market_id:
                payload[market_id] = row
        return payload

    @staticmethod
    def _columns_from_schema_payload(schema_payload: Mapping[str, Any]) -> tuple[AltFeatureColumn, ...]:
        raw_columns = schema_payload.get("columns")
        if not isinstance(raw_columns, list):
            return ()
        columns: list[AltFeatureColumn] = []
        for row in raw_columns:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "").strip()
            dtype = str(row.get("dtype") or "").strip()
            group = str(row.get("group") or "").strip()
            description = str(row.get("description") or "").strip()
            if not name or not dtype:
                continue
            columns.append(
                AltFeatureColumn(
                    name=name,
                    dtype=dtype,
                    group=group or "unknown",
                    description=description,
                )
            )
        return tuple(columns)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
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
