from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def deterministic_run_id() -> str:
    return "alpha-run-001"


@pytest.fixture
def temp_config_paths(tmp_path: Path) -> tuple[Path, Path]:
    app_src = Path("config/app.yaml")
    agents_src = Path("config/agents.yaml")

    with app_src.open("r", encoding="utf-8") as handle:
        app_cfg = yaml.safe_load(handle) or {}
    with agents_src.open("r", encoding="utf-8") as handle:
        agents_cfg = yaml.safe_load(handle) or {}

    artifacts_dir = tmp_path / "artifacts"
    audit_path = tmp_path / "audit" / "events.jsonl"
    app_cfg.setdefault("storage", {})
    app_cfg["storage"]["artifacts_dir"] = str(artifacts_dir)
    app_cfg["storage"].setdefault("audit_log", {})
    app_cfg["storage"]["audit_log"]["path"] = str(audit_path)

    app_dst = tmp_path / "app.yaml"
    agents_dst = tmp_path / "agents.yaml"
    app_dst.write_text(yaml.safe_dump(app_cfg, sort_keys=False), encoding="utf-8")
    agents_dst.write_text(yaml.safe_dump(agents_cfg, sort_keys=False), encoding="utf-8")
    return app_dst, agents_dst
