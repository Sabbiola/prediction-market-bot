from __future__ import annotations

from pathlib import Path

import yaml

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.secrets import CommandSecretProvider, EnvSecretProvider, SecretResolutionError, build_secret_provider


def test_env_secret_provider_resolves_required(monkeypatch: object) -> None:
    monkeypatch.setenv("TEST_SECRET_KEY", "secret-value")
    provider = EnvSecretProvider()
    assert provider.get("TEST_SECRET_KEY", required=True) == "secret-value"


def test_env_secret_provider_raises_on_missing_required() -> None:
    provider = EnvSecretProvider()
    try:
        provider.get("MISSING_SECRET_KEY", required=True)
    except SecretResolutionError as exc:
        assert "missing required secret" in str(exc)
    else:
        raise AssertionError("expected missing secret error")


def test_command_secret_provider_uses_env_fallback(monkeypatch: object) -> None:
    monkeypatch.setenv("FALLBACK_SECRET_KEY", "fallback-value")
    provider = CommandSecretProvider(command_template="python -c \"import sys; sys.exit(1)\"", timeout_sec=1.0)
    assert provider.get("FALLBACK_SECRET_KEY") == "fallback-value"


def test_build_secret_provider_from_settings(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("security", {})
    raw["security"]["secrets"] = {
        "backend": "env",
        "command_template": "",
        "command_timeout_sec": 5.0,
        "env_fallback": True,
    }
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    settings = load_settings(app_cfg, agents_cfg)
    provider = build_secret_provider(settings)
    assert isinstance(provider, EnvSecretProvider)
