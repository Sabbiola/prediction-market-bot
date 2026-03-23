from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
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


def _run_ids_in_operational_db(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT run_id FROM runs").fetchall()
    return {str(row[0]) for row in rows}


def test_cli_db_backup_restore_and_verify_roundtrip(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = _read_yaml(app_cfg)
    storage = raw.setdefault("storage", {})
    if not isinstance(storage, dict):
        raise AssertionError("storage section must be mapping")
    operational = storage.setdefault("operational_db", {})
    if not isinstance(operational, dict):
        raise AssertionError("storage.operational_db section must be mapping")

    backup_dir = tmp_path / "backups"
    operational["driver"] = "sqlite"
    operational["backup_dir"] = str(backup_dir)
    storage["operational_db"] = operational
    raw["storage"] = storage
    _write_yaml(app_cfg, raw)

    run_001 = "backup-test-run-001"
    run_002 = "backup-test-run-002"

    run_first_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_001,
        ]
    )
    assert run_first_exit == 0
    capsys.readouterr()

    backup_exit = main(
        [
            "db-backup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--label",
            "baseline",
            "--json",
        ]
    )
    assert backup_exit == 0
    backup_payload = json.loads(capsys.readouterr().out)
    backup_path = Path(str(backup_payload["backup_path"]))
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    assert backup_path.name.startswith("operational-")
    assert backup_path.name.endswith(".sqlite3")

    run_second_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_002,
        ]
    )
    assert run_second_exit == 0
    capsys.readouterr()

    db_path = Path(str(operational["path"]))
    run_ids_before_restore = _run_ids_in_operational_db(db_path)
    assert run_001 in run_ids_before_restore
    assert run_002 in run_ids_before_restore

    restore_blocked_exit = main(
        [
            "db-restore",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--backup-file",
            str(backup_path),
        ]
    )
    assert restore_blocked_exit == 1
    assert "--force" in capsys.readouterr().out

    restore_exit = main(
        [
            "db-restore",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--latest",
            "--force",
            "--json",
        ]
    )
    assert restore_exit == 0
    restore_payload = json.loads(capsys.readouterr().out)
    assert restore_payload["restore"]["backup_path"] == str(backup_path)
    assert restore_payload["restore"]["integrity_ok"] is True
    assert restore_payload["verify"]["ok"] is True

    verify_exit = main(
        [
            "db-verify",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True
    assert verify_payload["integrity_ok"] is True
    assert verify_payload["schema_up_to_date"] is True

    run_ids_after_restore = _run_ids_in_operational_db(db_path)
    assert run_001 in run_ids_after_restore
    assert run_002 not in run_ids_after_restore
