from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib import error

import pytest

from prediction_market_bot.infrastructure import PolymarketReadOnlyMarketDataAdapter
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

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            return self._payload
        return self._payload[:amount]


def _valid_market_payload(*, updated_at: str = "2026-03-13T11:55:00Z") -> list[dict[str, object]]:
    return [
        {
            "id": "live-1",
            "question": "Will event A happen?",
            "active": True,
            "closed": False,
            "resolved": False,
            "updatedAt": updated_at,
            "endDate": "2026-03-15T12:00:00Z",
            "outcomePrices": "[\"0.64\", \"0.36\"]",
            "liquidity": "120000",
            "volume24hr": "45000",
            "bestBid": "0.63",
            "bestAsk": "0.65",
            "priceChange24h": "0.01",
            "category": "politics",
        }
    ]


def test_fetch_batch_normalizes_and_persists_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(json.dumps(_valid_market_payload()))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=0,
        max_staleness_seconds=900,
        persistence=persistence,
        now_fn=lambda: now,
    )
    batch = adapter.fetch_batch(run_id="live-run-1", limit=5)

    assert batch.run_id == "live-run-1"
    assert batch.fetched_count == 1
    assert batch.normalized_count == 1
    assert batch.stale_count == 0
    assert batch.invalid_count == 0
    assert batch.retries_used == 0
    assert len(batch.snapshots) == 1

    snapshot = batch.snapshots[0]
    assert snapshot.market_id == "live-1"
    assert snapshot.yes_price == 0.64
    assert snapshot.spread_bps == 200
    assert snapshot.hours_to_resolution == 48.0

    raw_artifacts = [item for item in persistence.artifacts if item[1] == "raw_market_snapshots"]
    normalized_artifacts = [item for item in persistence.artifacts if item[1] == "normalized_market_snapshots"]
    assert len(raw_artifacts) == 1
    assert len(normalized_artifacts) == 1
    assert raw_artifacts[0][2]["payload"]["id"] == "live-1"
    assert normalized_artifacts[0][2]["market"]["market_id"] == "live-1"
    assert persistence.events[0][1] == "live_market_fetch_end"


def test_fetch_batch_retries_on_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    attempts = {"count": 0}

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise error.URLError("timed out")
        return _MockHttpResponse(json.dumps(_valid_market_payload()))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http_client_module.time, "sleep", lambda seconds: None)

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        max_staleness_seconds=900,
        now_fn=lambda: now,
    )
    batch = adapter.fetch_batch(run_id="live-run-2", limit=5)

    assert attempts["count"] == 2
    assert batch.retries_used == 1
    assert batch.normalized_count == 1


def test_fetch_batch_flags_stale_and_invalid_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    stale_row = _valid_market_payload(updated_at="2026-03-13T09:30:00Z")[0]
    invalid_row = dict(stale_row)
    invalid_row["updatedAt"] = "2026-03-13T11:59:00Z"
    del invalid_row["id"]
    payload = [stale_row, invalid_row]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(json.dumps(payload))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=0,
        max_staleness_seconds=900,
        persistence=persistence,
        now_fn=lambda: now,
    )
    batch = adapter.fetch_batch(run_id="live-run-3", limit=10)

    assert batch.fetched_count == 2
    assert batch.normalized_count == 0
    assert batch.stale_count == 1
    assert batch.invalid_count == 1

    raw_artifacts = [item for item in persistence.artifacts if item[1] == "raw_market_snapshots"]
    normalized_artifacts = [item for item in persistence.artifacts if item[1] == "normalized_market_snapshots"]
    assert len(raw_artifacts) == 2
    assert len(normalized_artifacts) == 0

    stale_reasons = [item[2]["stale_reason"] for item in raw_artifacts]
    invalid_reasons = [item[2]["invalid_reason"] for item in raw_artifacts]
    assert "stale_data" in stale_reasons
    assert "missing_required_fields" in invalid_reasons
    source_failures = [item for item in persistence.artifacts if item[1] == "source_failures"]
    assert any(item[2]["classification"] == "stale_data" for item in source_failures)
    assert any(item[2]["classification"] == "malformed_payload" for item in source_failures)


def test_fetch_batch_fails_on_403_and_persists_failure_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "https://example.test/markets")
        raise error.HTTPError(url, 403, "forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=0,
        max_staleness_seconds=900,
        persistence=persistence,
        now_fn=lambda: now,
    )
    with pytest.raises(RuntimeError, match="classification=blocked_403"):
        adapter.fetch_batch(run_id="live-run-403", limit=5)

    source_failures = [item for item in persistence.artifacts if item[1] == "source_failures"]
    assert any(item[2]["classification"] == "blocked_403" for item in source_failures)


def test_fetch_batch_fails_on_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(json.dumps({"unexpected": "shape"}))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=0,
        max_staleness_seconds=900,
        persistence=persistence,
        now_fn=lambda: now,
    )
    with pytest.raises(RuntimeError, match="classification=malformed_payload"):
        adapter.fetch_batch(run_id="live-run-malformed", limit=5)

    source_failures = [item for item in persistence.artifacts if item[1] == "source_failures"]
    assert any(item[2]["classification"] == "malformed_payload" for item in source_failures)


def test_fetch_batch_fails_on_missing_required_api_key() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    persistence = _InMemoryPersistence()

    adapter = PolymarketReadOnlyMarketDataAdapter(
        endpoint_url="https://example.test/markets",
        timeout_sec=2.0,
        max_retries=0,
        max_staleness_seconds=900,
        require_api_key=True,
        api_key="",
        persistence=persistence,
        now_fn=lambda: now,
    )
    with pytest.raises(RuntimeError, match="classification=auth_config_error"):
        adapter.fetch_batch(run_id="live-run-missing-key", limit=5)

    source_failures = [item for item in persistence.artifacts if item[1] == "source_failures"]
    assert any(item[2]["classification"] == "auth_config_error" for item in source_failures)
