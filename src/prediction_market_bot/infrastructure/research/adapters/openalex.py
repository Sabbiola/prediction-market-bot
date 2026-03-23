from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping
from urllib import parse

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import ResearchFinding
from prediction_market_bot.infrastructure.http_client import StructuredHttpClient

from ..base import StructuredHttpResearchSource
from ..models import ResearchNormalizationContext, ResearchSourcePayloadError


class OpenAlexWorksResearchSource(StructuredHttpResearchSource):
    def __init__(
        self,
        *,
        endpoint_url: str = "https://api.openalex.org/works",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        cache_ttl_seconds: int = 600,
        retry_jitter_sec: float = 0.25,
        headers: Mapping[str, str] | None = None,
        api_key: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        require_api_key: bool = False,
        now_fn: Callable[[], datetime] | None = None,
        http_client: StructuredHttpClient | None = None,
    ) -> None:
        super().__init__(
            source_name="openalex-works",
            source_type=SourceType.OFFICIAL,
            endpoint_url=endpoint_url,
            base_confidence=0.72,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            cache_ttl_seconds=cache_ttl_seconds,
            retry_jitter_sec=retry_jitter_sec,
            headers=headers,
            api_key=api_key,
            api_key_header=api_key_header,
            api_key_prefix=api_key_prefix,
            require_api_key=require_api_key,
            now_fn=now_fn,
            http_client=http_client,
        )

    def build_url(self, *, query: str, limit: int) -> str:
        params = {
            "search": query,
            "per-page": str(max(limit, 1)),
            "sort": "relevance_score:desc",
        }
        querystring = parse.urlencode(params)
        separator = "&" if "?" in self.endpoint_url else "?"
        return f"{self.endpoint_url}{separator}{querystring}"

    def extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ResearchSourcePayloadError(
                source_name=self.source_name,
                reason_code="payload_not_mapping",
                message="openalex_payload_not_mapping",
            )
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise ResearchSourcePayloadError(
                source_name=self.source_name,
                reason_code="results_missing_or_invalid",
                message="openalex_results_missing_or_invalid",
            )
        return [row for row in rows if isinstance(row, Mapping)]

    def normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        context: ResearchNormalizationContext,
    ) -> ResearchFinding | None:
        title = self.normalizer.first_str(record, ("display_name", "title"))
        if title is None:
            return None

        primary_location = record.get("primary_location")
        landing_page_url = ""
        venue_name = ""
        if isinstance(primary_location, Mapping):
            landing_page_url = self.normalizer.first_str(primary_location, ("landing_page_url", "pdf_url")) or ""
            source_info = primary_location.get("source")
            if isinstance(source_info, Mapping):
                venue_name = self.normalizer.first_str(source_info, ("display_name",)) or ""

        concepts = record.get("concepts")
        concept_name = ""
        if isinstance(concepts, list):
            for item in concepts:
                if isinstance(item, Mapping):
                    concept_name = self.normalizer.first_str(item, ("display_name",)) or ""
                    if concept_name:
                        break

        summary_parts = [title]
        if concept_name:
            summary_parts.append(f"Topic: {concept_name}")
        if venue_name:
            summary_parts.append(f"Venue: {venue_name}")
        summary = " | ".join(summary_parts)

        published_at = self.normalizer.parse_timestamp(record.get("publication_date"))
        record_id = self.normalizer.first_str(record, ("id", "doi")) or "unknown"
        url = landing_page_url or record_id
        return self.normalizer.build_finding(
            context=context,
            summary=summary,
            url=url,
            relevance_text=f"{title} {concept_name} {venue_name}",
            published_at=published_at,
            base_confidence=self.base_confidence,
            provenance_extra=(
                f"record_id={record_id}",
                f"concept={concept_name}",
            ),
        )

    def use_openalex_auth(self) -> bool:
        return True
