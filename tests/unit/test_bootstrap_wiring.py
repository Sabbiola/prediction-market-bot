from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from prediction_market_bot.app.bootstrap import (
    build_coordinator,
    build_http_client,
    build_market_data_provider,
    build_operational_repositories,
    build_persistence,
)
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.infrastructure import PolymarketReadOnlyMarketDataAdapter, StaticMarketDataProvider


def test_build_http_client_reads_openalex_key_from_env(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    monkeypatch.setenv(settings.http.openalex_api_key_env, "test-openalex-key")

    client = build_http_client(
        settings,
        timeout_sec=1.5,
        max_retries=1,
        retry_backoff_sec=0.2,
        retry_jitter_sec=0.1,
        cache_ttl_sec=5,
    )

    assert client.openalex_api_key == "test-openalex-key"
    assert client.timeout_sec == 1.5
    assert client.max_retries == 1
    assert client.retry_backoff_sec == 0.2
    assert client.retry_jitter_sec == 0.1
    assert client.cache_ttl_sec == 5


def test_build_coordinator_wires_static_runtime_path(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("runtime", {})["mode"] = "DRY_RUN_STATIC"
    raw.setdefault("runtime", {})["market_data_provider"] = "STATIC"
    raw.setdefault("runtime", {})["research_provider"] = "STATIC"
    raw.setdefault("execution", {})["review_auto_approve"] = False
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)

    coordinator = build_coordinator(settings, persistence)

    assert isinstance(coordinator.market_data, StaticMarketDataProvider)
    assert coordinator.execution.execution_mode == settings.execution.mode
    assert coordinator.settlement_same_run == settings.execution.settlement_same_run
    if settings.enable_manual_review_queue:
        assert coordinator.review_queue_hook is not None
    else:
        assert coordinator.review_queue_hook is None
    assert coordinator.review_gate_hook is None


def test_build_market_data_provider_uses_live_adapter_in_paper_live_mode(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    runtime = raw.setdefault("runtime", {})
    runtime["mode"] = "PAPER_LIVE"
    runtime["market_data_provider"] = "AUTO"
    runtime["provider_failure_policy"] = "FAIL_FAST"  # avoid fallback wrapper for isinstance check
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)
    http_client = build_http_client(settings)
    provider = build_market_data_provider(settings, persistence, http_client)

    assert isinstance(provider, PolymarketReadOnlyMarketDataAdapter)


def test_build_operational_repositories_postgres_requires_dsn(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AssertionError("app config must be mapping")
    storage = raw.setdefault("storage", {})
    if not isinstance(storage, dict):
        raise AssertionError("storage config must be mapping")
    operational_db = storage.setdefault("operational_db", {})
    if not isinstance(operational_db, dict):
        raise AssertionError("storage.operational_db must be mapping")
    operational_db["driver"] = "postgres"
    operational_db["dsn"] = ""
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    settings = load_settings(app_cfg, agents_cfg)
    with pytest.raises(ValueError, match="storage\\.operational_db\\.dsn"):
        build_operational_repositories(settings)
