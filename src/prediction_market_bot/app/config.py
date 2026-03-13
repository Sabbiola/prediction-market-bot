from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from prediction_market_bot.app.settings import AppSettings


def load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return dict(raw)


def load_settings(app_config_path: str | Path, agents_config_path: str | Path) -> AppSettings:
    app_path = Path(app_config_path)
    agents_path = Path(agents_config_path)

    app_config = load_yaml_file(app_path)
    agents_config = load_yaml_file(agents_path)
    settings = AppSettings.from_dicts(app_config=app_config, agents_config=agents_config)
    allow_live_execution = bool((app_config.get("feature_flags") or {}).get("allow_live_execution", False))
    settings.validate_dry_run_only(allow_live_execution=allow_live_execution)
    return settings
