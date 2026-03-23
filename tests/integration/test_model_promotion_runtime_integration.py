from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.app.bootstrap import build_operational_repositories
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.main import main
from prediction_market_bot.services import load_operator_state, operator_state_path, save_operator_state


def _configure_runtime_gate_case(
    app_cfg: Path,
    agents_cfg: Path,
    *,
    runtime_mode: str,
    engine: str = "model_v2",
    fallback_to_heuristic: bool,
) -> None:
    app_payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    app_payload.setdefault("runtime", {})
    app_payload["runtime"]["mode"] = runtime_mode
    app_payload["runtime"]["market_data_provider"] = "STATIC"
    app_payload["runtime"]["research_provider"] = "STATIC"
    app_payload.setdefault("execution", {})
    app_payload["execution"]["review_auto_approve"] = True
    app_payload.setdefault("feature_flags", {})
    app_payload["feature_flags"]["enable_manual_review_queue"] = True
    app_cfg.write_text(yaml.safe_dump(app_payload, sort_keys=False), encoding="utf-8")

    agents_payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    agents_payload.setdefault("agents", {})
    prediction_cfg = agents_payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    model_inference = prediction_cfg.setdefault("model_inference", {})  # type: ignore[assignment]
    model_inference["engine"] = engine
    model_inference["fallback_to_heuristic"] = fallback_to_heuristic
    model_inference["model_artifact_path"] = ""
    agents_cfg.write_text(yaml.safe_dump(agents_payload, sort_keys=False), encoding="utf-8")


def _read_prediction_rationales(artifacts_dir: Path, run_id: str) -> list[tuple[str, ...]]:
    path = artifacts_dir / "prediction_results.jsonl"
    if not path.exists():
        return []
    rows: list[tuple[str, ...]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("run_id") != run_id:
            continue
        payload = raw.get("payload", {})
        rationale = payload.get("rationale", [])
        if isinstance(rationale, list):
            rows.append(tuple(str(item) for item in rationale))
    return rows


def test_run_once_blocks_when_model_v2_not_promoted_and_no_fallback(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_runtime_gate_case(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        fallback_to_heuristic=False,
    )
    exit_code = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            "gate-blocked-run",
        ]
    )
    assert exit_code == 1


def test_run_once_falls_back_to_heuristic_when_model_v2_not_promoted(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_runtime_gate_case(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        fallback_to_heuristic=True,
    )
    run_id = "gate-fallback-run"
    exit_code = main(
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
    assert exit_code == 0

    settings = load_settings(app_cfg, agents_cfg)
    rationales = _read_prediction_rationales(Path(settings.storage.artifacts_dir), run_id)
    assert rationales
    assert any(
        any("forced_heuristic_reason=model_v2_not_enabled_for_runtime_mode" in reason for reason in rationale)
        for rationale in rationales
    )


def test_runtime_rollback_forces_heuristic_engine_safely(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_runtime_gate_case(
        app_cfg,
        agents_cfg,
        runtime_mode="DRY_RUN_STATIC",
        fallback_to_heuristic=False,
    )
    settings = load_settings(app_cfg, agents_cfg)
    repos = build_operational_repositories(settings)
    state_path = operator_state_path(settings.storage.artifacts_dir)
    state = load_operator_state(state_path, repository=repos.operator_control_state)
    state.model_v2_rollback_active = True
    state.model_v2_rollback_reason = "runtime_incident_test"
    save_operator_state(state_path, state, repository=repos.operator_control_state)

    run_id = "rollback-safe-run"
    exit_code = main(
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
    assert exit_code == 0

    rationales = _read_prediction_rationales(Path(settings.storage.artifacts_dir), run_id)
    assert rationales
    assert any(
        any("forced_heuristic_reason=model_v2_rollback_active" in reason for reason in rationale)
        for rationale in rationales
    )
