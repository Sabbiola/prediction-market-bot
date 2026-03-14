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


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_cli_migrations_upgrade_schema_and_unblock_startup(
    temp_config_paths: tuple[Path, Path],
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = _read_yaml(app_cfg)
    storage = raw.setdefault("storage", {})
    if not isinstance(storage, dict):
        raise AssertionError("storage section must be mapping")
    operational = storage.setdefault("operational_db", {})
    if not isinstance(operational, dict):
        raise AssertionError("storage.operational_db section must be mapping")
    operational["driver"] = "sqlite"
    operational["auto_migrate_on_boot"] = False
    operational["require_up_to_date"] = True
    storage["operational_db"] = operational
    raw["storage"] = storage
    _write_yaml(app_cfg, raw)

    version_exit = main(
        [
            "db-current-version",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert version_exit == 2
    version_payload = json.loads(capsys.readouterr().out)
    assert version_payload["up_to_date"] is False
    assert version_payload["pending_versions"]

    validate_before_exit = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert validate_before_exit == 1
    validate_before = json.loads(capsys.readouterr().out)
    failed_names = {row["name"] for row in validate_before["checks"] if not row["ok"]}
    assert "operational_schema_version" in failed_names

    upgrade_exit = main(
        [
            "db-upgrade",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert upgrade_exit == 0
    upgrade_payload = json.loads(capsys.readouterr().out)
    assert upgrade_payload["up_to_date"] is True
    assert upgrade_payload["pending_versions"] == []

    validate_after_exit = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert validate_after_exit == 0
    validate_after = json.loads(capsys.readouterr().out)
    assert validate_after["ok"] is True
