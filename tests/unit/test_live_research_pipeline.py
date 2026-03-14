from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib import error

import pytest

from prediction_market_bot.domain.models import MarketSnapshot
from prediction_market_bot.infrastructure import LiveResearchIngestionPipeline, build_live_research_sources
from prediction_market_bot.infrastructure import http_client as http_client_module


class _InMemoryPersistence:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, Mapping[str, Any]]] = []
        self.artifacts: list[tuple[str, str, Mapping[str, Any]]] = []

    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        self.events.append((run_id, event_type, payload))

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        self.artifacts.append((run_id, artifact_type, payload))


class _MockHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_MockHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _market() -> MarketSnapshot:
    return MarketSnapshot.from_yes_price(
        market_id="research-market-1",
        venue="polymarket",
        title="Will inflation decline in the next quarter?",
        yes_price=0.52,
        liquidity_usd=25_000.0,
        volume_24h_usd=10_000.0,
        spread_bps=120,
        hours_to_resolution=48.0,
        last_price_move_bps=20,
        category="macro",
    )


def test_research_ingestion_normalizes_deduplicates_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 11,
                    "title": "Inflation outlook",
                    "snippet": "Inflation decline forecast for next quarter.",
                    "timestamp": "2026-03-13T10:00:00Z",
                },
                {
                    "pageid": 12,
                    "title": "Interest rates",
                    "snippet": "Rates remain restrictive amid uncertainty.",
                    "timestamp": "2026-03-10T10:00:00Z",
                },
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "display_name": "Inflation decline forecast for next quarter.",
                "publication_date": "2026-03-01",
                "primary_location": {
                    "landing_page_url": "https://example.org/papers/123",
                    "source": {"display_name": "Macro Journal"},
                },
                "concepts": [{"display_name": "Inflation"}],
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=600,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, persistence=persistence, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-run-1", query_override="inflation decline", limit_per_source=5)

    assert batch.run_id == "research-run-1"
    assert batch.source_count == 2
    assert batch.raw_count == 3
    assert batch.normalized_count == 3
    assert batch.deduplicated_count == 2
    assert all(finding.provenance for finding in batch.findings)
    assert any("duplicate_source=" in item for finding in batch.findings for item in finding.provenance)

    raw_rows = [row for row in persistence.artifacts if row[1] == "raw_research_findings"]
    normalized_rows = [row for row in persistence.artifacts if row[1] == "normalized_research_findings"]
    dedup_rows = [row for row in persistence.artifacts if row[1] == "deduped_research_findings"]
    assert len(raw_rows) == 3
    assert len(normalized_rows) == 3
    assert len(dedup_rows) == 2
    assert persistence.events[0][1] == "research_ingestion_end"


def test_research_ingestion_uses_http_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    calls = {"count": 0}

    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 11,
                    "title": "Inflation outlook",
                    "snippet": "Inflation decline forecast for next quarter.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "display_name": "Inflation decline forecast for next quarter.",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        calls["count"] += 1
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=600,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, now_fn=lambda: now)
    first = pipeline.ingest(_market(), run_id="research-cache-1")
    second = pipeline.ingest(_market(), run_id="research-cache-2")

    assert first.cache_hits == 0
    assert second.cache_hits == 2
    assert calls["count"] == 2


def test_research_ingestion_retries_after_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    attempts = {"wikipedia": 0}

    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 99,
                    "title": "Inflation outlook",
                    "snippet": "Inflation decline forecast for next quarter.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W222",
                "display_name": "Macro inflation survey",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            attempts["wikipedia"] += 1
            if attempts["wikipedia"] == 1:
                raise error.URLError("temporary timeout")
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_client_module.time, "sleep", lambda seconds: None)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        cache_ttl_seconds=0,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-retry-1", query_override="inflation")

    assert attempts["wikipedia"] == 2
    assert batch.retries_used == 1
    assert batch.deduplicated_count >= 1


def test_research_ingestion_persists_timeout_source_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W999",
                "display_name": "Backup openalex record",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            raise error.URLError("timed out")
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=0,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, persistence=persistence, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-timeout-1", query_override="inflation")

    assert batch.source_failures == 1
    source_failures = [row for row in persistence.artifacts if row[1] == "source_failures"]
    assert any(row[2]["classification"] == "timeout" for row in source_failures)


def test_research_ingestion_persists_403_source_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W998",
                "display_name": "Backup openalex record",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            raise error.HTTPError(url, 403, "forbidden", hdrs=None, fp=None)
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=0,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, persistence=persistence, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-403-1", query_override="inflation")

    assert batch.source_failures == 1
    source_failures = [row for row in persistence.artifacts if row[1] == "source_failures"]
    assert any(row[2]["classification"] == "blocked_403" for row in source_failures)


def test_research_ingestion_persists_malformed_payload_source_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W333",
                "display_name": "Macro inflation survey",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps({"query": {"unexpected": []}}))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=0,
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, persistence=persistence, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-malformed-1", query_override="inflation")

    assert batch.source_failures == 1
    source_failures = [row for row in persistence.artifacts if row[1] == "source_failures"]
    assert any(row[2]["classification"] == "malformed_payload" for row in source_failures)


def test_research_ingestion_applies_per_source_headers_and_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    captured_headers: dict[str, dict[str, str]] = {}

    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 99,
                    "title": "Inflation outlook",
                    "snippet": "Inflation decline forecast for next quarter.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W222",
                "display_name": "Macro inflation survey",
                "publication_date": "2026-03-01",
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        header_items = {str(key).lower(): str(value) for key, value in getattr(req, "header_items")()}
        if "wikipedia.test" in url:
            captured_headers["wikipedia"] = header_items
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            captured_headers["openalex"] = header_items
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    sources = build_live_research_sources(
        wikipedia_endpoint_url="https://wikipedia.test/api.php",
        openalex_endpoint_url="https://openalex.test/works",
        timeout_sec=2.0,
        max_retries=0,
        cache_ttl_seconds=0,
        wikipedia_headers={"X-Wiki-Header": "wiki"},
        wikipedia_api_key="wiki-key",
        wikipedia_api_key_header="X-Wiki-Key",
        wikipedia_api_key_prefix="",
        openalex_headers={"X-Openalex-Header": "openalex"},
        openalex_api_key="openalex-key",
        openalex_api_key_header="X-Openalex-Key",
        openalex_api_key_prefix="",
        now_fn=lambda: now,
    )
    pipeline = LiveResearchIngestionPipeline(sources=sources, now_fn=lambda: now)
    batch = pipeline.ingest(_market(), run_id="research-headers-1", query_override="inflation")

    assert batch.source_failures == 0
    assert captured_headers["wikipedia"]["x-wiki-header"] == "wiki"
    assert captured_headers["wikipedia"]["x-wiki-key"] == "wiki-key"
    assert captured_headers["openalex"]["x-openalex-header"] == "openalex"
    assert captured_headers["openalex"]["x-openalex-key"] == "openalex-key"
