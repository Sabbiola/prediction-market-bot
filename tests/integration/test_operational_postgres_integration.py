from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
import yaml

from prediction_market_bot.main import main


def _read_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise AssertionError("Expected YAML mapping")
    return dict(payload)


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _postgres_test_dsn() -> str:
    dsn = os.getenv("PM_BOT_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.skip("PM_BOT_TEST_POSTGRES_DSN is not configured.")
    if importlib.util.find_spec("psycopg") is None:
        pytest.skip("psycopg is not installed; postgres integration test skipped.")
    return dsn


def test_operational_postgres_run_flow(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    dsn = _postgres_test_dsn()
    app_cfg, agents_cfg = temp_config_paths
    raw = _read_yaml(app_cfg)
    storage = raw.setdefault("storage", {})
    if not isinstance(storage, dict):
        raise AssertionError("storage section must be mapping")
    operational = storage.setdefault("operational_db", {})
    if not isinstance(operational, dict):
        raise AssertionError("storage.operational_db must be mapping")
    operational["driver"] = "postgres"
    operational["dsn"] = dsn
    operational["auto_migrate_on_boot"] = False
    operational["require_up_to_date"] = True
    storage["operational_db"] = operational
    raw["storage"] = storage
    _write_yaml(app_cfg, raw)

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
    assert upgrade_payload["dialect"] == "postgres"
    assert upgrade_payload["up_to_date"] is True

    validate_exit = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert validate_exit == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["ok"] is True

    run_id = "postgres-flow-run-001"
    run_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_id,
        ]
    )
    assert run_exit == 0
    capsys.readouterr()

    psycopg = importlib.import_module("psycopg")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT payload_json FROM runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
    assert row is not None
