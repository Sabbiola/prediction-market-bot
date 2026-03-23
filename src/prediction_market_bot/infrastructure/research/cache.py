from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from prediction_market_bot.infrastructure.http_client import StructuredHttpClient


@dataclass(slots=True, frozen=True)
class ResearchFetchPolicy:
    timeout_sec: float
    max_retries: int
    retry_backoff_sec: float
    retry_jitter_sec: float
    cache_ttl_seconds: int
    headers: Mapping[str, str]
    api_key: str
    api_key_header: str
    api_key_prefix: str
    require_api_key: bool
    add_openalex_auth: bool = False


@dataclass(slots=True, frozen=True)
class ResearchFetchResult:
    payload: Any
    retries_used: int
    cache_hit: bool


def fetch_json_with_policy(
    *,
    http_client: StructuredHttpClient,
    source_name: str,
    url: str,
    policy: ResearchFetchPolicy,
) -> ResearchFetchResult:
    response = http_client.fetch_json(
        source=source_name,
        url=url,
        headers=policy.headers,
        use_cache=policy.cache_ttl_seconds > 0,
        cache_ttl_sec=policy.cache_ttl_seconds,
        timeout_sec=policy.timeout_sec,
        max_retries=policy.max_retries,
        retry_backoff_sec=policy.retry_backoff_sec,
        retry_jitter_sec=policy.retry_jitter_sec,
        add_openalex_auth=policy.add_openalex_auth,
        api_key=policy.api_key,
        api_key_header=policy.api_key_header,
        api_key_prefix=policy.api_key_prefix,
        require_api_key=policy.require_api_key,
    )
    return ResearchFetchResult(
        payload=response.payload,
        retries_used=response.retries_used,
        cache_hit=response.cache_hit,
    )
