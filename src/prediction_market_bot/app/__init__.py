"""Application bootstrap components.

This package exposes convenience imports while keeping heavy bootstrap wiring lazy
to avoid import-time cycles across services/app modules.
"""

from __future__ import annotations

from typing import Any

from .config import load_settings
from .settings import AppSettings

_BOOTSTRAP_EXPORTS = {
    "build_coordinator",
    "build_http_client",
    "build_live_market_data_provider",
    "build_live_research_pipeline",
    "build_market_data_provider",
    "build_operational_repositories",
    "build_persistence",
    "build_research_sources",
}

__all__ = ["AppSettings", "load_settings", *_BOOTSTRAP_EXPORTS]


def __getattr__(name: str) -> Any:
    if name in _BOOTSTRAP_EXPORTS:
        from . import bootstrap

        return getattr(bootstrap, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
