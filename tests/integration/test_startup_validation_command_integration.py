from __future__ import annotations

import json
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


def _copy_staging_profile(tmp_path: Path) -> tuple[Path, Path]:
    app_src = Path("config/app.staging.yaml")
    agents_src = Path("config/agents.yaml")
    raw = _read_yaml(app_src)
    storage = raw.setdefault("storage", {})
    if not isinstance(storage, dict):
        raise AssertionError("storage section must be mapping")
    operational_db = storage.setdefault("operational_db", {})
    if not isinstance(operational_db, dict):
        raise AssertionError("storage.operational_db section must be mapping")
    operational_db["path"] = str(tmp_path / "db" / "runtime.db")
    storage["artifacts_dir"] = str(tmp_path / "artifacts")
    audit = storage.setdefault("audit_log", {})
    if not isinstance(audit, dict):
        raise AssertionError("storage.audit_log section must be mapping")
    audit["path"] = str(tmp_path / "audit" / "events.jsonl")
    observability = raw.setdefault("observability", {})
    if not isinstance(observability, dict):
        raise AssertionError("observability section must be mapping")
    metrics = observability.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        raise AssertionError("observability.metrics section must be mapping")
    metrics["path"] = str(tmp_path / "metrics" / "metrics.prom")
    app_dst = tmp_path / "app.staging.yaml"
    app_dst.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    agents_dst = tmp_path / "agents.yaml"
    agents_dst.write_text(agents_src.read_text(encoding="utf-8"), encoding="utf-8")
    return app_dst, agents_dst


def test_validate_startup_fails_when_sandbox_submit_tx_missing_required_config(
    temp_config_paths: tuple[Path, Path],
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = _read_yaml(app_cfg)
    sandbox = raw.setdefault("sandbox_chain", {})
    if not isinstance(sandbox, dict):
        raise AssertionError("sandbox_chain section must be mapping")
    sandbox["enabled"] = True
    sandbox["submit_tx"] = True
    sandbox["rpc_url"] = ""
    sandbox["contract_address"] = ""
    sandbox["allow_unlocked_send"] = False
    sandbox["private_key_env"] = "SANDBOX_CHAIN_PRIVATE_KEY"
    raw["sandbox_chain"] = sandbox
    _write_yaml(app_cfg, raw)

    exit_code = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    failed_names = {row["name"] for row in payload["checks"] if not row["ok"]}
    assert "sandbox_chain_rpc_url" in failed_names
    assert "sandbox_chain_contract_address" in failed_names


def test_validate_startup_fails_when_alt_data_oauth_source_has_missing_credential(
    temp_config_paths: tuple[Path, Path],
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = _read_yaml(app_cfg)
    raw["alt_data"] = {
        "enabled": True,
        "sources": {
            "reddit": {
                "enabled": True,
                "source_class": "reddit",
                "adapter": "reddit_oauth_adapter",
                "credential_env": "REDDIT_ACCESS_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            }
        },
    }
    _write_yaml(app_cfg, raw)

    exit_code = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    checks = {row["name"]: row for row in payload["checks"]}
    assert checks["alt_data_sources"]["ok"] is False
    assert "REDDIT_ACCESS_TOKEN" in checks["alt_data_sources"]["detail"]


def test_validate_startup_includes_staging_profile_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    app_cfg, agents_cfg = _copy_staging_profile(tmp_path)
    monkeypatch.delenv("OPERATIONAL_DB_DSN", raising=False)
    monkeypatch.delenv("SANDBOX_CHAIN_PRIVATE_KEY", raising=False)

    exit_code = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {row["name"]: row for row in payload["checks"]}
    assert checks["staging_profile_runtime_mode"]["ok"] is True
    assert checks["staging_profile_execution_mode"]["ok"] is True
    assert checks["staging_profile_live_providers"]["ok"] is True
    assert checks["staging_profile_review_gate"]["ok"] is True
    assert checks["staging_profile_sandbox_tx"]["ok"] is True
    assert checks["staging_profile_settlement_lane"]["ok"] is True
    assert checks["staging_profile_ui_auth"]["ok"] is True
    assert checks["staging_profile_operational_db"]["ok"] is True


def test_validate_startup_fails_for_incomplete_staging_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    app_cfg, agents_cfg = _copy_staging_profile(tmp_path)
    raw = _read_yaml(app_cfg)
    runtime = raw.setdefault("runtime", {})
    execution = raw.setdefault("execution", {})
    ui_auth = raw.setdefault("ui_auth", {})
    if not isinstance(runtime, dict) or not isinstance(execution, dict) or not isinstance(ui_auth, dict):
        raise AssertionError("runtime/execution/ui_auth sections must be mapping")
    runtime["mode"] = "PAPER_LIVE"
    execution["mode"] = "PAPER"
    execution["settlement_same_run"] = True
    ui_auth["enabled"] = False
    _write_yaml(app_cfg, raw)
    monkeypatch.delenv("OPERATIONAL_DB_DSN", raising=False)
    monkeypatch.delenv("SANDBOX_CHAIN_PRIVATE_KEY", raising=False)

    exit_code = main(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {row["name"]: row for row in payload["checks"]}
    assert checks["staging_profile_runtime_mode"]["ok"] is False
    assert checks["staging_profile_execution_mode"]["ok"] is False
    assert checks["staging_profile_settlement_lane"]["ok"] is False
    assert checks["staging_profile_ui_auth"]["ok"] is False
    assert "SANDBOX_CHAIN" in checks["staging_profile_runtime_mode"]["detail"]
    assert "settlement_same_run_false" in checks["staging_profile_settlement_lane"]["detail"]
