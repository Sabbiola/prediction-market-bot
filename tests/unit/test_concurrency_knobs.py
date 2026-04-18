"""Tests for run-loop concurrency knob parsing and startup warning (REC-09)."""

from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from prediction_market_bot.app.config import load_settings


def _write_config(tmp_path: Path, run_loop: dict) -> tuple[Path, Path]:
    app_cfg = tmp_path / "app.yaml"
    agents_cfg = tmp_path / "agents.yaml"
    agents_cfg.write_text("agents: {}", encoding="utf-8")
    app_cfg.write_text(
        yaml.safe_dump({"app": {"run_loop": run_loop}}, sort_keys=False),
        encoding="utf-8",
    )
    return app_cfg, agents_cfg


def test_settings_load_concurrency_knobs_from_config(tmp_path: Path) -> None:
    app_cfg, agents_cfg = _write_config(
        tmp_path,
        {
            "scan_interval_sec": 300,
            "settlement_interval_sec": 900,
            "max_parallel_research_jobs": 4,
            "max_parallel_prediction_jobs": 2,
        },
    )
    settings = load_settings(app_cfg, agents_cfg)
    assert settings.runtime.scan_interval_sec == 300
    assert settings.runtime.settlement_interval_sec == 900
    assert settings.runtime.max_parallel_research_jobs == 4
    assert settings.runtime.max_parallel_prediction_jobs == 2


def test_settings_concurrency_knobs_default_to_one(tmp_path: Path) -> None:
    app_cfg, agents_cfg = _write_config(tmp_path, {})
    settings = load_settings(app_cfg, agents_cfg)
    assert settings.runtime.max_parallel_research_jobs == 1
    assert settings.runtime.max_parallel_prediction_jobs == 1
    assert settings.runtime.settlement_interval_sec == 600


def test_startup_warns_when_concurrency_above_one(temp_config_paths: tuple[Path, Path]) -> None:
    from prediction_market_bot.app.config import load_settings
    from prediction_market_bot.services.startup import _check_concurrency_knobs

    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("app", {}).setdefault("run_loop", {})
    raw["app"]["run_loop"]["max_parallel_research_jobs"] = 4
    raw["app"]["run_loop"]["max_parallel_prediction_jobs"] = 1
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    settings = load_settings(app_cfg, agents_cfg)
    checks = _check_concurrency_knobs(settings)

    severities = {c.name: c.severity for c in checks}
    assert severities["concurrency_knob_max_parallel_research_jobs"] == "warning"
    assert severities["concurrency_knob_max_parallel_prediction_jobs"] == "info"
    # Warning check must still report ok=True (not a blocker)
    assert all(c.ok for c in checks)


def test_startup_no_warning_when_concurrency_is_one(temp_config_paths: tuple[Path, Path]) -> None:
    from prediction_market_bot.app.config import load_settings
    from prediction_market_bot.services.startup import _check_concurrency_knobs

    app_cfg, agents_cfg = temp_config_paths
    # Explicitly set both to 1 so the fixture's inherited app.yaml values don't interfere.
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("app", {}).setdefault("run_loop", {})
    raw["app"]["run_loop"]["max_parallel_research_jobs"] = 1
    raw["app"]["run_loop"]["max_parallel_prediction_jobs"] = 1
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    settings = load_settings(app_cfg, agents_cfg)
    checks = _check_concurrency_knobs(settings)
    assert all(c.severity == "info" for c in checks)
    assert all(c.ok for c in checks)
