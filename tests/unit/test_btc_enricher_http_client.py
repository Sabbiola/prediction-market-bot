"""Regression tests for BTC enricher HTTP client injection (REC-05).

Guards against regressing to direct ``urllib.request`` calls that bypass the
shared ``StructuredHttpClient`` — which is where allowed-hosts enforcement,
retry/backoff and circuit breakers live.
"""

from __future__ import annotations

import types
from typing import Any

from prediction_market_bot.agents.btc_feature_enricher import (
    BtcFeatureEnricher,
    _safe_json_get,
)


class _StubHttpClient:
    """Minimal stub mimicking ``StructuredHttpClient.fetch_json`` surface."""

    def __init__(self, *, payloads: dict[str, Any]) -> None:
        self._payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def fetch_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        url = kwargs["url"]
        for prefix, payload in self._payloads.items():
            if url.startswith(prefix):
                return types.SimpleNamespace(payload=payload)
        return types.SimpleNamespace(payload=None)


def test_safe_json_get_routes_through_http_client() -> None:
    client = _StubHttpClient(payloads={"https://example.com": {"ok": True}})
    result = _safe_json_get("https://example.com/path", http_client=client)
    assert result == {"ok": True}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://example.com/path"
    # Enricher has its own TTL cache — must not duplicate in the HTTP client.
    assert call["use_cache"] is False
    assert call["source"] == "btc_feature_enricher"


def test_safe_json_get_swallows_client_errors() -> None:
    class Boom:
        def fetch_json(self, **kwargs: Any) -> Any:
            raise RuntimeError("network down")

    # Must not raise — upstream code expects `None` on failure.
    assert _safe_json_get("https://example.com", http_client=Boom()) is None


def test_btc_enricher_accepts_http_client_and_stores_it() -> None:
    client = _StubHttpClient(payloads={})
    enricher = BtcFeatureEnricher(http_client=client)
    assert enricher._http_client is client


def test_btc_enricher_default_http_client_is_none() -> None:
    # Backward-compatibility: callers that don't pass a client still work.
    enricher = BtcFeatureEnricher()
    assert enricher._http_client is None


def test_btc_enricher_get_features_uses_injected_http_client(monkeypatch) -> None:
    """When get_features is called with an injected client, the Binance klines
    endpoint is routed through the client and not via urllib.
    """
    # Minimal synthetic klines payload — 200 candles of monotonic prices.
    candles = []
    for i in range(200):
        candles.append(
            [
                (1_700_000_000 + i * 300) * 1000,  # open_time_ms
                "50000.0",  # open
                "50010.0",  # high
                "49990.0",  # low
                "50005.0",  # close
                "1.0",      # volume
                0, 0, 0, 0, 0, 0,  # rest unused
            ]
        )
    client = _StubHttpClient(
        payloads={
            "https://api.binance.com/api/v3/klines": candles,
            "https://fapi.binance.com": [],
            "https://api.binance.com/api/v3/depth": {"bids": [], "asks": []},
            "https://api.alternative.me": {"data": [{"value": 50}]},
            "https://api.coinbase.com": {"data": {"amount": "50000"}},
            "https://api.kraken.com": {"result": {}, "error": []},
        }
    )

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("urllib path must not be used when http_client is injected")

    monkeypatch.setattr("prediction_market_bot.agents.btc_feature_enricher.urllib.request.urlopen", _explode)

    enricher = BtcFeatureEnricher(http_client=client)
    feats = enricher.get_features()
    assert isinstance(feats, dict)
    assert "f_btc_rsi_14" in feats
    # At least one call must have routed to the Binance klines endpoint.
    assert any(c["url"].startswith("https://api.binance.com/api/v3/klines") for c in client.calls)
