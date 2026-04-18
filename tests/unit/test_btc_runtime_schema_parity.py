"""Parity tests for BTC runtime feature schema negotiation (REC-01).

These tests guard against the regression where ``enrich_with_btc_features``
hardcoded ``"btc-v2-15m"`` and therefore broke parity against a promoted
``btc-v2-5m`` model artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prediction_market_bot.agents.prediction import _parse_btc_interval
from prediction_market_bot.agents.prediction_runtime_features import (
    RuntimePredictionFeatures,
    enrich_with_btc_features,
)


def _base_features() -> RuntimePredictionFeatures:
    return RuntimePredictionFeatures(
        schema_version="v1",
        decision_timestamp_utc=datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC),
        values={
            "f_market_yes_price": 0.52,
            "f_market_no_price": 0.48,
        },
    )


def _btc_features() -> dict[str, float]:
    return {
        "f_btc_rsi14": 55.0,
        "f_btc_macd_hist": 0.12,
        "f_btc_fear_greed": 67.0,
    }


@pytest.mark.parametrize(
    "expected_schema",
    ["btc-v2-5m", "btc-v2-15m"],
)
def test_enrich_with_btc_features_adopts_caller_schema_version(expected_schema: str) -> None:
    enriched = enrich_with_btc_features(
        _base_features(),
        _btc_features(),
        schema_version=expected_schema,
    )
    assert enriched.schema_version == expected_schema
    # Base values must be preserved.
    assert enriched.values["f_market_yes_price"] == pytest.approx(0.52)
    # BTC features must be merged in.
    assert enriched.values["f_btc_rsi14"] == pytest.approx(55.0)
    assert enriched.values["f_btc_fear_greed"] == pytest.approx(67.0)


def test_enrich_with_btc_features_returns_base_when_no_btc_features() -> None:
    base = _base_features()
    result = enrich_with_btc_features(base, {}, schema_version="btc-v2-5m")
    assert result is base  # unchanged object identity is fine here


def test_enrich_with_btc_features_rejects_empty_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        enrich_with_btc_features(
            _base_features(),
            _btc_features(),
            schema_version="   ",
        )


def test_enrich_with_btc_features_strips_whitespace_schema_version() -> None:
    enriched = enrich_with_btc_features(
        _base_features(),
        _btc_features(),
        schema_version="  btc-v2-5m  ",
    )
    assert enriched.schema_version == "btc-v2-5m"


@pytest.mark.parametrize(
    "schema_version,expected_interval",
    [
        ("btc-v2-5m", "5m"),
        ("btc-v2-15m", "15m"),
        ("btc-v2-1h", "5m"),  # non-minute suffix falls back to default
        ("", "5m"),
        ("v1", "5m"),
        ("btc-experimental-3m-beta", "3m"),
    ],
)
def test_parse_btc_interval(schema_version: str, expected_interval: str) -> None:
    assert _parse_btc_interval(schema_version) == expected_interval


def test_enrich_with_btc_features_preserves_decision_timestamp() -> None:
    base = _base_features()
    enriched = enrich_with_btc_features(
        base,
        _btc_features(),
        schema_version="btc-v2-15m",
    )
    assert enriched.decision_timestamp_utc == base.decision_timestamp_utc
