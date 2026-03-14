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
