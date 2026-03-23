from __future__ import annotations

from dataclasses import dataclass

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.infrastructure.operational_sqlite import OperationalRepositories
from prediction_market_bot.infrastructure.persistence import JsonlPersistence


@dataclass(slots=True, frozen=True)
class UiRuntimeContext:
    settings: AppSettings
    persistence: JsonlPersistence
    operational: OperationalRepositories
    config_path: str
    agents_config_path: str


def build_ui_runtime_context(config_path: str, agents_config_path: str) -> UiRuntimeContext:
    settings = load_settings(config_path, agents_config_path)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    return UiRuntimeContext(
        settings=settings,
        persistence=persistence,
        operational=operational,
        config_path=config_path,
        agents_config_path=agents_config_path,
    )
