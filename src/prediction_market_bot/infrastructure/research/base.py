from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.interfaces import ResearchDataPort
from prediction_market_bot.infrastructure.http_client import StructuredHttpClient

from .cache import ResearchFetchPolicy, fetch_json_with_policy
from .models import ResearchNormalizationContext, SourceFetchBatch
from .normalizer import ResearchFindingNormalizer


class StructuredHttpResearchSource(ResearchDataPort, ABC):
    """Base class for read-only structured research sources over HTTP JSON APIs."""

    def __init__(
        self,
        *,
        source_name: str,
        source_type: SourceType,
        endpoint_url: str,
        base_confidence: float,
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        cache_ttl_seconds: int = 300,
        retry_jitter_sec: float = 0.25,
        headers: Mapping[str, str] | None = None,
        api_key: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        require_api_key: bool = False,
        now_fn: Callable[[], datetime] | None = None,
        http_client: StructuredHttpClient | None = None,
        normalizer: ResearchFindingNormalizer | None = None,
    ) -> None:
        self.source_name = source_name
        self.source_type = source_type
        self.endpoint_url = endpoint_url
        self.base_confidence = ResearchFindingNormalizer.clamp(base_confidence, lower=0.05, upper=1.0)
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self.retry_jitter_sec = max(retry_jitter_sec, 0.0)
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self.api_key = api_key.strip()
        self.api_key_header = api_key_header.strip()
        self.api_key_prefix = api_key_prefix
        self.require_api_key = require_api_key
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.normalizer = normalizer or ResearchFindingNormalizer()
        self.http_client = http_client or StructuredHttpClient(
            user_agent="prediction-market-bot/0.1",
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            cache_ttl_sec=self.cache_ttl_seconds,
            now_fn=self.now_fn,
        )

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        return self.fetch_with_meta(market).findings

    def fetch_with_meta(
        self,
        market: MarketSnapshot,
        *,
        query_override: str | None = None,
        limit: int = 5,
    ) -> SourceFetchBatch:
        started = perf_counter()
        query = query_override.strip() if isinstance(query_override, str) and query_override.strip() else self.build_query(market)
        payload, retries_used, cache_hit = self._fetch_payload(query=query, limit=limit)
        records = self.extract_records(payload)
        fetched_at = self.now_fn()
        context = ResearchNormalizationContext(
            market=market,
            query=query,
            fetched_at=fetched_at,
            source_name=self.source_name,
            source_type=self.source_type,
            endpoint_url=self.endpoint_url,
        )

        findings: list[ResearchFinding] = []
        raw_records: list[dict[str, Any]] = []
        for record in records:
            raw_records.append(dict(record))
            finding = self.normalize_record(record, context=context)
            if finding is not None:
                findings.append(finding)

        ordered = tuple(
            sorted(
                findings,
                key=lambda finding: (
                    -finding.credibility,
                    finding.source_name,
                    finding.summary,
                    finding.url,
                ),
            )
        )
        return SourceFetchBatch(
            source_name=self.source_name,
            source_type=self.source_type,
            query=query,
            raw_count=len(raw_records),
            normalized_count=len(ordered),
            retries_used=retries_used,
            cache_hit=cache_hit,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            findings=ordered,
            raw_records=tuple(raw_records),
        )

    def _fetch_payload(self, *, query: str, limit: int) -> tuple[Any, int, bool]:
        url = self.build_url(query=query, limit=limit)
        policy = ResearchFetchPolicy(
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            cache_ttl_seconds=self.cache_ttl_seconds,
            headers=self.headers,
            api_key=self.api_key,
            api_key_header=self.api_key_header,
            api_key_prefix=self.api_key_prefix,
            require_api_key=self.require_api_key,
            add_openalex_auth=self.use_openalex_auth(),
        )
        result = fetch_json_with_policy(
            http_client=self.http_client,
            source_name=self.source_name,
            url=url,
            policy=policy,
        )
        return result.payload, result.retries_used, result.cache_hit

    def build_query(self, market: MarketSnapshot) -> str:
        return self.normalizer.build_query(market.title, market.market_id)

    def use_openalex_auth(self) -> bool:
        return False

    @abstractmethod
    def build_url(self, *, query: str, limit: int) -> str:
        raise NotImplementedError

    @abstractmethod
    def extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        context: ResearchNormalizationContext,
    ) -> ResearchFinding | None:
        raise NotImplementedError
