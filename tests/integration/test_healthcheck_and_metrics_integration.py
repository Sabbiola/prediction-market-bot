from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.main import main


def _read_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise AssertionError("Expected YAML mapping")
    return dict(raw)


def test_healthcheck_command_reports_startup_and_metrics(
    temp_config_paths: tuple[Path, Path],
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    exit_code = main(
        [
            "healthcheck",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["checks"]
    assert "metrics" in payload
    assert "review_queue_depth" in payload["metrics"]
    assert "tx_pending_count" in payload["metrics"]


def test_status_command_writes_prometheus_metrics_textfile(
    temp_config_paths: tuple[Path, Path],
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    cfg = _read_yaml(app_cfg)
    metrics_path = Path(
        str(cfg.get("observability", {}).get("metrics", {}).get("path", ""))  # type: ignore[union-attr]
    )
    if metrics_path.exists():
        metrics_path.unlink()

    exit_code = main(
        [
            "status",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 0
    _ = json.loads(capsys.readouterr().out)
    assert metrics_path.exists()
    metrics_text = metrics_path.read_text(encoding="utf-8")
    assert "pm_bot_review_queue_depth" in metrics_text
    assert "pm_bot_tx_pending" in metrics_text
