from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.infrastructure.alt_data import (
    AltDataAdapterRegistry,
    AltDataCapabilityError,
    AltDataCredentialError,
    SourceOperation,
    build_credential_resolver_from_mapping,
)


def _set_alt_data_sources(app_cfg: Path, sources: dict[str, object], *, enabled: bool = True) -> None:
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload["alt_data"] = {
        "enabled": bool(enabled),
        "sources": sources,
    }
    app_cfg.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_alt_data_registry_registration_is_config_driven(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_alt_data_sources(
        app_cfg,
        {
            "news_rss_web": {
                "enabled": True,
                "source_class": "news_rss_web",
                "adapter": "rss_news_adapter",
                "capabilities": {
                    "requires_oauth": False,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": False,
                },
            },
            "reddit": {
                "enabled": False,
                "source_class": "reddit",
                "adapter": "reddit_oauth_adapter",
                "credential_env": "REDDIT_ACCESS_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            },
            "x": {
                "enabled": True,
                "source_class": "x",
                "adapter": "x_api_adapter",
                "credential_env": "X_BEARER_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            },
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=build_credential_resolver_from_mapping({"X_BEARER_TOKEN": "token-123"}),
    )

    assert settings.alt_data.enabled is True
    assert registry.list_registered_ids() == ("news_rss_web", "reddit", "x")
    assert registry.list_enabled_ids() == ("news_rss_web", "x")

    ready_news = registry.require_source_ready("news_rss_web", operation=SourceOperation.SEARCH)
    assert ready_news.source_id == "news_rss_web"
    ready_x = registry.require_source_ready("x", operation=SourceOperation.SEARCH)
    assert ready_x.source_id == "x"


def test_alt_data_registry_fails_clearly_when_oauth_credential_missing(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_alt_data_sources(
        app_cfg,
        {
            "reddit": {
                "enabled": True,
                "source_class": "reddit",
                "adapter": "reddit_oauth_adapter",
                "credential_env": "REDDIT_ACCESS_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            }
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=build_credential_resolver_from_mapping({}),
    )

    with pytest.raises(AltDataCredentialError, match="REDDIT_ACCESS_TOKEN"):
        registry.require_source_ready("reddit", operation=SourceOperation.SEARCH)


def test_alt_data_registry_fails_when_capability_is_not_supported(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_alt_data_sources(
        app_cfg,
        {
            "news_rss_web": {
                "enabled": True,
                "source_class": "news_rss_web",
                "adapter": "rss_news_adapter",
                "capabilities": {
                    "requires_oauth": False,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": False,
                    "supports_thread_context_expansion": False,
                },
            }
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    registry = AltDataAdapterRegistry.from_source_settings(settings.alt_data.sources)

    with pytest.raises(AltDataCapabilityError, match="does_not_support operation=search"):
        registry.require_source_ready("news_rss_web", operation=SourceOperation.SEARCH)


def test_alt_data_registry_fails_clearly_when_x_credential_missing(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_alt_data_sources(
        app_cfg,
        {
            "x": {
                "enabled": True,
                "source_class": "x",
                "adapter": "x_api_adapter",
                "credential_env": "X_BEARER_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            }
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=build_credential_resolver_from_mapping({}),
    )

    with pytest.raises(AltDataCredentialError, match="X_BEARER_TOKEN"):
        registry.require_source_ready("x", operation=SourceOperation.SEARCH)


def test_alt_data_registry_fails_when_x_thread_context_capability_is_missing(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_alt_data_sources(
        app_cfg,
        {
            "x": {
                "enabled": True,
                "source_class": "x",
                "adapter": "x_api_adapter",
                "credential_env": "X_BEARER_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": False,
                },
            }
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    registry = AltDataAdapterRegistry.from_source_settings(
        settings.alt_data.sources,
        credential_resolver=build_credential_resolver_from_mapping({"X_BEARER_TOKEN": "token-123"}),
    )

    with pytest.raises(AltDataCapabilityError, match="thread_context_expansion"):
        registry.require_source_ready("x", operation=SourceOperation.THREAD_CONTEXT_EXPANSION)
