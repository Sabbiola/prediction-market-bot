from pathlib import Path

import pytest

from prediction_market_bot.app.config import load_settings


def test_load_settings_from_repository_config() -> None:
    settings = load_settings(
        app_config_path=Path("config/app.yaml"),
        agents_config_path=Path("config/agents.yaml"),
    )
    assert settings.runtime.mode == "dry-run"
    assert settings.dry_run is True
    assert settings.prediction.min_confidence > 0.0
    assert settings.risk.bankroll_usd > 0.0


def test_rejects_live_mode(tmp_path: Path) -> None:
    app_cfg = tmp_path / "app.yaml"
    agents_cfg = tmp_path / "agents.yaml"

    app_cfg.write_text(
        "\n".join(
            [
                "app:",
                "  mode: live",
                "observability:",
                "  log_level: INFO",
                "  json_logs: true",
                "venue:",
                "  provider: polymarket",
                "  dry_run: true",
                "feature_flags:",
                "  allow_live_execution: false",
            ]
        ),
        encoding="utf-8",
    )
    agents_cfg.write_text("thresholds: {}\nrisk: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run"):
        load_settings(app_cfg, agents_cfg)
