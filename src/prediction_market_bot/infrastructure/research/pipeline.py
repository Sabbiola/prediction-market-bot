from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.interfaces import PersistencePort, ResearchDataPort
from prediction_market_bot.infrastructure.http_client import (
    HttpClientError,
    HttpErrorMetadata,
    StructuredHttpClient,
)

from .adapters import OpenAlexWorksResearchSource, WikipediaSearchResearchSource
from .base import StructuredHttpResearchSource
from .models import ResearchIngestionBatch, ResearchSourcePayloadError, SourceFetchBatch
from .normalizer import ResearchFindingNormalizer


class LiveResearchIngestionPipeline(ResearchDataPort):
    """Aggregates structured research sources with deduplication and traceability."""

    def __init__(
        self,
        *,
        sources: Sequence[StructuredHttpResearchSource],
        persistence: PersistencePort | None = None,
        now_fn: Callable[[], datetime] | None = None,
        similarity_threshold: float = 0.7,
        default_limit_per_source: int = 5,
    ) -> None:
        self.sources = tuple(sources)
        self.persistence = persistence
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.similarity_threshold = ResearchFindingNormalizer.clamp(similarity_threshold, lower=0.5, upper=1.0)
        self.default_limit_per_source = max(default_limit_per_source, 1)

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        return self.ingest(
            market,
            run_id=None,
            limit_per_source=self.default_limit_per_source,
        ).findings

    def ingest(
        self,
        market: MarketSnapshot,
        *,
        run_id: str | None = None,
        query_override: str | None = None,
        limit_per_source: int = 5,
    ) -> ResearchIngestionBatch:
        ingestion_started = perf_counter()
        effective_run_id = run_id or f"research-ingest-{self.now_fn().strftime('%Y%m%d%H%M%S')}"
        normalized: list[ResearchFinding] = []
        raw_count = 0
        retries_used = 0
        cache_hits = 0
        source_failures = 0
        source_total_duration_ms = 0.0

        for source in self.sources:
            try:
                batch = source.fetch_with_meta(
                    market,
                    query_override=query_override,
                    limit=max(limit_per_source, 1),
                )
            except HttpClientError as exc:
                source_failures += 1
                self._persist_http_error(
                    effective_run_id,
                    market.market_id,
                    source_name=source.source_name,
                    metadata=exc.metadata,
                )
                continue
            except ResearchSourcePayloadError as exc:
                source_failures += 1
                self._persist_source_failure(
                    effective_run_id,
                    market.market_id,
                    source_name=source.source_name,
                    source_type=source.source_type.value,
                    classification="malformed_payload",
                    reason_code=exc.reason_code,
                    message=exc.message,
                )
                continue
            except Exception as exc:
                source_failures += 1
                self._persist_source_failure(
                    effective_run_id,
                    market.market_id,
                    source_name=source.source_name,
                    source_type=source.source_type.value,
                    classification="unknown_error",
                    reason_code=type(exc).__name__,
                    message=str(exc),
                )
                continue
            raw_count += batch.raw_count
            retries_used += batch.retries_used
            cache_hits += int(batch.cache_hit)
            source_total_duration_ms += max(batch.duration_ms, 0.0)
            normalized.extend(batch.findings)
            self._persist_source_batch(effective_run_id, market.market_id, batch)

        deduplicated = self._deduplicate(normalized)
        for finding in deduplicated:
            self._persist_normalized(effective_run_id, market.market_id, finding)

        self._write_event(
            effective_run_id,
            {
                "market_id": market.market_id,
                "source_count": len(self.sources),
                "source_failures": source_failures,
                "raw_count": raw_count,
                "normalized_count": len(normalized),
                "deduplicated_count": len(deduplicated),
                "retries_used": retries_used,
                "cache_hits": cache_hits,
                "source_total_duration_ms": round(source_total_duration_ms, 3),
                "ingestion_duration_ms": round(max((perf_counter() - ingestion_started) * 1000.0, 0.0), 3),
            },
        )

        return ResearchIngestionBatch(
            run_id=effective_run_id,
            market_id=market.market_id,
            source_count=len(self.sources),
            source_failures=source_failures,
            raw_count=raw_count,
            normalized_count=len(normalized),
            deduplicated_count=len(deduplicated),
            retries_used=retries_used,
            cache_hits=cache_hits,
            source_total_duration_ms=round(source_total_duration_ms, 3),
            ingestion_duration_ms=round(max((perf_counter() - ingestion_started) * 1000.0, 0.0), 3),
            findings=deduplicated,
        )

    def _deduplicate(self, findings: Sequence[ResearchFinding]) -> tuple[ResearchFinding, ...]:
        ordered = sorted(findings, key=lambda item: (-item.credibility, item.source_name, item.summary, item.url))
        deduplicated: list[ResearchFinding] = []
        signatures: list[set[str]] = []

        for finding in ordered:
            tokens = self._tokenize(finding.summary)
            match_index = self._find_similar(finding, tokens, deduplicated, signatures)
            if match_index is None:
                deduplicated.append(finding)
                signatures.append(tokens)
                continue

            merged = self._merge_duplicates(deduplicated[match_index], finding)
            deduplicated[match_index] = merged
            signatures[match_index] = self._tokenize(merged.summary)

        return tuple(sorted(deduplicated, key=lambda item: (-item.credibility, item.source_name, item.summary, item.url)))

    def _find_similar(
        self,
        finding: ResearchFinding,
        tokens: set[str],
        existing: Sequence[ResearchFinding],
        signatures: Sequence[set[str]],
    ) -> int | None:
        for index, current in enumerate(existing):
            if finding.url and current.url and finding.url == current.url:
                return index
            similarity = self._jaccard(tokens, signatures[index])
            if similarity >= self.similarity_threshold:
                return index
            current_summary = current.summary.lower()
            new_summary = finding.summary.lower()
            if current_summary in new_summary or new_summary in current_summary:
                return index
        return None

    def _merge_duplicates(self, primary: ResearchFinding, duplicate: ResearchFinding) -> ResearchFinding:
        merged_provenance = ResearchFindingNormalizer.unique_strings(
            (
                *primary.provenance,
                *duplicate.provenance,
                f"duplicate_source={duplicate.source_name}",
            )
        )
        merged_confidence = round(
            ResearchFindingNormalizer.clamp(
                max(primary.credibility, duplicate.credibility) + (min(primary.credibility, duplicate.credibility) * 0.08),
                lower=0.05,
                upper=1.0,
            ),
            4,
        )
        merged_url = primary.url or duplicate.url
        return primary.model_copy(
            update={
                "credibility": merged_confidence,
                "url": merged_url,
                "provenance": merged_provenance,
            }
        )

    def _persist_source_batch(self, run_id: str, market_id: str, batch: SourceFetchBatch) -> None:
        if not self.persistence:
            return
        self._write_event(
            run_id,
            {
                "market_id": market_id,
                "source_name": batch.source_name,
                "source_type": batch.source_type.value,
                "query": batch.query,
                "raw_count": batch.raw_count,
                "normalized_count": batch.normalized_count,
                "retries_used": batch.retries_used,
                "cache_hit": batch.cache_hit,
                "duration_ms": round(batch.duration_ms, 3),
            },
            event_type="research_ingestion_source_end",
        )
        for row in batch.raw_records:
            self.persistence.write_artifact(
                run_id,
                "raw_research_findings",
                {
                    "market_id": market_id,
                    "source_name": batch.source_name,
                    "source_type": batch.source_type.value,
                    "query": batch.query,
                    "payload": row,
                },
            )
        for finding in batch.findings:
            self.persistence.write_artifact(
                run_id,
                "normalized_research_findings",
                {
                    "market_id": market_id,
                    "source_name": batch.source_name,
                    "source_type": batch.source_type.value,
                    "finding": finding.model_dump(mode="json"),
                },
            )

    def _persist_normalized(self, run_id: str, market_id: str, finding: ResearchFinding) -> None:
        if not self.persistence:
            return
        self.persistence.write_artifact(
            run_id,
            "deduped_research_findings",
            {
                "market_id": market_id,
                "finding": finding.model_dump(mode="json"),
            },
        )

    def _write_event(
        self,
        run_id: str,
        payload: Mapping[str, Any],
        *,
        event_type: str = "research_ingestion_end",
    ) -> None:
        if self.persistence:
            self.persistence.write_run_event(run_id, event_type, payload)

    def _persist_http_error(
        self,
        run_id: str,
        market_id: str,
        *,
        source_name: str,
        metadata: HttpErrorMetadata,
    ) -> None:
        if not self.persistence:
            return
        payload = {
            "market_id": market_id,
            "source_name": source_name,
            "http_error": metadata.to_dict(),
        }
        self.persistence.write_artifact(run_id, "http_error_metadata", payload)
        self.persistence.write_run_event(run_id, "research_ingestion_source_failed", payload)
        self._persist_source_failure(
            run_id,
            market_id,
            source_name=source_name,
            source_type="HTTP",
            classification=metadata.classification,
            reason_code=metadata.reason_code,
            message=metadata.message,
            details={
                "status_code": metadata.status_code,
                "retryable": metadata.retryable,
                "error_type": metadata.error_type,
            },
        )

    def _persist_source_failure(
        self,
        run_id: str,
        market_id: str,
        *,
        source_name: str,
        source_type: str,
        classification: str,
        reason_code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.persistence:
            return
        payload: dict[str, Any] = {
            "market_id": market_id,
            "source_name": source_name,
            "source_type": source_type,
            "classification": classification,
            "reason_code": reason_code,
            "message": message,
            "timestamp": self.now_fn().isoformat(),
        }
        if details:
            payload["details"] = dict(details)
        self.persistence.write_artifact(run_id, "source_failures", payload)
        self.persistence.write_run_event(run_id, "research_ingestion_source_failed", payload)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return ResearchFindingNormalizer.tokenize(text)

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)


def build_live_research_sources(
    *,
    wikipedia_endpoint_url: str = "https://en.wikipedia.org/w/api.php",
    openalex_endpoint_url: str = "https://api.openalex.org/works",
    timeout_sec: float = 8.0,
    max_retries: int = 2,
    retry_backoff_sec: float = 0.5,
    retry_jitter_sec: float = 0.25,
    cache_ttl_seconds: int = 600,
    wikipedia_timeout_sec: float | None = None,
    wikipedia_max_retries: int | None = None,
    wikipedia_retry_backoff_sec: float | None = None,
    wikipedia_retry_jitter_sec: float | None = None,
    wikipedia_cache_ttl_seconds: int | None = None,
    wikipedia_headers: Mapping[str, str] | None = None,
    wikipedia_api_key: str = "",
    wikipedia_api_key_header: str = "Authorization",
    wikipedia_api_key_prefix: str = "Bearer ",
    wikipedia_require_api_key: bool = False,
    openalex_timeout_sec: float | None = None,
    openalex_max_retries: int | None = None,
    openalex_retry_backoff_sec: float | None = None,
    openalex_retry_jitter_sec: float | None = None,
    openalex_cache_ttl_seconds: int | None = None,
    openalex_headers: Mapping[str, str] | None = None,
    openalex_api_key: str = "",
    openalex_api_key_header: str = "Authorization",
    openalex_api_key_prefix: str = "Bearer ",
    openalex_require_api_key: bool = False,
    enabled_sources: Sequence[str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    http_client: StructuredHttpClient | None = None,
) -> tuple[StructuredHttpResearchSource, ...]:
    enabled = {item.strip().lower() for item in enabled_sources} if enabled_sources is not None else None
    sources: list[StructuredHttpResearchSource] = []

    if enabled is None or "wikipedia" in enabled or "wikipedia-search" in enabled:
        sources.append(
            WikipediaSearchResearchSource(
                endpoint_url=wikipedia_endpoint_url,
                timeout_sec=wikipedia_timeout_sec if wikipedia_timeout_sec is not None else timeout_sec,
                max_retries=wikipedia_max_retries if wikipedia_max_retries is not None else max_retries,
                retry_backoff_sec=(
                    wikipedia_retry_backoff_sec if wikipedia_retry_backoff_sec is not None else retry_backoff_sec
                ),
                retry_jitter_sec=(
                    wikipedia_retry_jitter_sec if wikipedia_retry_jitter_sec is not None else retry_jitter_sec
                ),
                cache_ttl_seconds=(
                    wikipedia_cache_ttl_seconds if wikipedia_cache_ttl_seconds is not None else cache_ttl_seconds
                ),
                headers=wikipedia_headers,
                api_key=wikipedia_api_key,
                api_key_header=wikipedia_api_key_header,
                api_key_prefix=wikipedia_api_key_prefix,
                require_api_key=wikipedia_require_api_key,
                now_fn=now_fn,
                http_client=http_client,
            )
        )

    if enabled is None or "openalex" in enabled or "openalex-works" in enabled:
        sources.append(
            OpenAlexWorksResearchSource(
                endpoint_url=openalex_endpoint_url,
                timeout_sec=openalex_timeout_sec if openalex_timeout_sec is not None else timeout_sec,
                max_retries=openalex_max_retries if openalex_max_retries is not None else max_retries,
                retry_backoff_sec=(
                    openalex_retry_backoff_sec if openalex_retry_backoff_sec is not None else retry_backoff_sec
                ),
                retry_jitter_sec=(
                    openalex_retry_jitter_sec if openalex_retry_jitter_sec is not None else retry_jitter_sec
                ),
                cache_ttl_seconds=(
                    openalex_cache_ttl_seconds if openalex_cache_ttl_seconds is not None else cache_ttl_seconds
                ),
                headers=openalex_headers,
                api_key=openalex_api_key,
                api_key_header=openalex_api_key_header,
                api_key_prefix=openalex_api_key_prefix,
                require_api_key=openalex_require_api_key,
                now_fn=now_fn,
                http_client=http_client,
            )
        )

    return tuple(sources)
