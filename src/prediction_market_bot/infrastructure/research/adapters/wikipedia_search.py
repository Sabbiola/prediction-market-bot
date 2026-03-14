from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping
from urllib import parse

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import ResearchFinding
from prediction_market_bot.infrastructure.http_client import StructuredHttpClient

from ..base import StructuredHttpResearchSource
from ..models import ResearchNormalizationContext, ResearchSourcePayloadError


class WikipediaSearchResearchSource(StructuredHttpResearchSource):
    def __init__(
        self,
        *,
        endpoint_url: str = "https://en.wikipedia.org/w/api.php",
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
            source_name="wikipedia-search",
            source_type=SourceType.RSS,
            endpoint_url=endpoint_url,
            base_confidence=0.58,
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
            "action": "query",
            "list": "search",
            "format": "json",
            "utf8": "1",
            "srsearch": query,
            "srlimit": str(max(limit, 1)),
        }
        querystring = parse.urlencode(params)
        separator = "&" if "?" in self.endpoint_url else "?"
        return f"{self.endpoint_url}{separator}{querystring}"

    def extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ResearchSourcePayloadError(
                source_name=self.source_name,
                reason_code="payload_not_mapping",
                message="wikipedia_payload_not_mapping",
            )
        query_section = payload.get("query")
        if not isinstance(query_section, Mapping):
            raise ResearchSourcePayloadError(
                source_name=self.source_name,
                reason_code="query_section_missing_or_invalid",
                message="wikipedia_query_section_missing_or_invalid",
            )
        rows = query_section.get("search")
        if not isinstance(rows, list):
            raise ResearchSourcePayloadError(
                source_name=self.source_name,
                reason_code="search_rows_missing_or_invalid",
                message="wikipedia_search_rows_missing_or_invalid",
            )
        return [row for row in rows if isinstance(row, Mapping)]

    def normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        context: ResearchNormalizationContext,
    ) -> ResearchFinding | None:
        title = self.normalizer.first_str(record, ("title",))
        snippet_raw = self.normalizer.first_str(record, ("snippet",)) or ""
        snippet = self.normalizer.strip_html(snippet_raw)
        summary = snippet or (title or "")
        if not summary:
            return None

        page_id = record.get("pageid")
        page_id_text = str(page_id).strip() if page_id is not None else ""
        url = f"https://en.wikipedia.org/?curid={page_id_text}" if page_id_text else ""
        published_at = self.normalizer.parse_timestamp(record.get("timestamp"))
        return self.normalizer.build_finding(
            context=context,
            summary=summary,
            url=url,
            relevance_text=f"{title or ''} {summary}",
            published_at=published_at,
            base_confidence=self.base_confidence,
            provenance_extra=(
                f"record_id={page_id_text or 'unknown'}",
                f"title={title or 'unknown'}",
            ),
        )
