from __future__ import annotations

from pathlib import Path

import yaml

from prediction_market_bot.app.bootstrap import build_operational_repositories
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.main import main
from prediction_market_bot.services import PaperPortfolioEngine, load_operator_state, operator_state_path


def _configure_deferred_profitable(app_cfg: Path, agents_cfg: Path) -> None:
    app_payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    app_payload.setdefault("execution", {})
    app_payload["execution"]["settlement_same_run"] = False
    app_payload["execution"]["blocking_trade_review"] = False
    app_cfg.write_text(yaml.safe_dump(app_payload, sort_keys=False), encoding="utf-8")

    agents_payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    agents_payload.setdefault("thresholds", {})
    agents_payload["thresholds"]["min_confidence"] = 0.0
    agents_payload["thresholds"]["min_edge_bps"] = -10000
    agents_payload.setdefault("agents", {})
    prediction_cfg = agents_payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    prediction_components = prediction_cfg.setdefault("components", {})  # type: ignore[assignment]
    prediction_components["market_weight"] = 0.0
    prediction_components["narrative_weight"] = 0.8
    prediction_components["structure_weight"] = 0.2
    agents_payload.setdefault("risk", {})
    agents_payload["risk"]["min_bet_usd"] = 1.0
    agents_cfg.write_text(yaml.safe_dump(agents_payload, sort_keys=False), encoding="utf-8")


def test_operational_sqlite_persists_open_positions_and_pending_settlements(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_deferred_profitable(app_cfg, agents_cfg)

    run_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert run_exit == 0

    settings = load_settings(app_cfg, agents_cfg)
    repos = build_operational_repositories(settings)

    run_rows = repos.pending_settlements.list_requests(run_id=deterministic_run_id, state=None, limit=0)
    assert run_rows
    assert any(row.state.value == "PENDING" for row in run_rows)

    snapshot_payload = repos.open_positions.load_snapshot()
    assert isinstance(snapshot_payload, dict)
    assert int(snapshot_payload.get("open_position_count", 0)) >= 1

    restored = PaperPortfolioEngine(open_positions_repo=repos.open_positions)
    restored_ok = restored.restore_from_repository()
    assert restored_ok is True
    assert restored.snapshot().open_position_count >= 1


def test_operator_control_state_persists_in_operational_sqlite(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    pause_exit = main(
        [
            "pause",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--reason",
            "sqlite-state-test",
        ]
    )
    assert pause_exit == 0

    settings = load_settings(app_cfg, agents_cfg)
    repos = build_operational_repositories(settings)
    state = load_operator_state(
        operator_state_path(settings.storage.artifacts_dir),
        repository=repos.operator_control_state,
    )
    assert state.paused is True
    assert state.pause_reason == "sqlite-state-test"

    resume_exit = main(
        [
            "resume",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
        ]
    )
    assert resume_exit == 0

    state_after = load_operator_state(
        operator_state_path(settings.storage.artifacts_dir),
        repository=repos.operator_control_state,
    )
    assert state_after.paused is False
