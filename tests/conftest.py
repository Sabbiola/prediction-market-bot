from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
import yaml

from prediction_market_bot.infrastructure.operational_sqlite import close_pool_for_path


@pytest.fixture
def deterministic_run_id() -> str:
    return "alpha-run-001"


@pytest.fixture
def temp_config_paths(tmp_path: Path) -> Generator[tuple[Path, Path], None, None]:
    app_src = Path("config/app.yaml")
    agents_src = Path("config/agents.yaml")

    with app_src.open("r", encoding="utf-8") as handle:
        app_cfg = yaml.safe_load(handle) or {}
    with agents_src.open("r", encoding="utf-8") as handle:
        agents_cfg = yaml.safe_load(handle) or {}

    artifacts_dir = tmp_path / "artifacts"
    audit_path = tmp_path / "audit" / "events.jsonl"
    runtime_db = tmp_path / "db" / "runtime.db"
    app_cfg.setdefault("storage", {})
    app_cfg["storage"].setdefault("operational_db", {})
    app_cfg["storage"]["operational_db"]["path"] = str(runtime_db)
    app_cfg["storage"]["artifacts_dir"] = str(artifacts_dir)
    app_cfg["storage"].setdefault("audit_log", {})
    app_cfg["storage"]["audit_log"]["path"] = str(audit_path)
    app_cfg.setdefault("observability", {})
    app_cfg["observability"].setdefault("metrics", {})
    app_cfg["observability"]["metrics"]["path"] = str(tmp_path / "metrics" / "metrics.prom")
    app_cfg["observability"].setdefault("logs", {})
    app_cfg["observability"]["logs"]["file_path"] = ""
    app_cfg.setdefault("execution", {})
    app_cfg["execution"]["settlement_same_run"] = True
    app_cfg["execution"]["blocking_trade_review"] = False

    app_dst = tmp_path / "app.yaml"
    agents_dst = tmp_path / "agents.yaml"
    app_dst.write_text(yaml.safe_dump(app_cfg, sort_keys=False), encoding="utf-8")
    agents_dst.write_text(yaml.safe_dump(agents_cfg, sort_keys=False), encoding="utf-8")
    yield app_dst, agents_dst
    # Release pooled SQLite connections so tmp_path cleanup can delete the DB file (Windows).
    close_pool_for_path(runtime_db)
