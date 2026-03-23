from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.services.model_promotion import (
    gate_required_for_runtime_mode,
    resolve_runtime_model_gate_decision,
)
from prediction_market_bot.services.operator_control import OperatorControlState


def _configure_prediction_engine(
    app_cfg: Path,
    agents_cfg: Path,
    *,
    runtime_mode: str,
    engine: str,
    fallback_to_heuristic: bool,
    model_artifact_path: str = "",
) -> None:
    app_payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    app_payload.setdefault("runtime", {})
    app_payload["runtime"]["mode"] = runtime_mode
    app_cfg.write_text(yaml.safe_dump(app_payload, sort_keys=False), encoding="utf-8")

    agents_payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    agents_payload.setdefault("agents", {})
    prediction_cfg = agents_payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    model_inference = prediction_cfg.setdefault("model_inference", {})  # type: ignore[assignment]
    model_inference["engine"] = engine
    model_inference["fallback_to_heuristic"] = fallback_to_heuristic
    model_inference["model_artifact_path"] = model_artifact_path
    agents_cfg.write_text(yaml.safe_dump(agents_payload, sort_keys=False), encoding="utf-8")


def test_gate_required_in_paper_live_mode(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2",
        fallback_to_heuristic=True,
    )
    settings = load_settings(app_cfg, agents_cfg)
    assert gate_required_for_runtime_mode(settings) is True


def test_not_promoted_model_v2_falls_back_when_enabled(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2",
        fallback_to_heuristic=True,
    )
    settings = load_settings(app_cfg, agents_cfg)
    decision = resolve_runtime_model_gate_decision(settings=settings, state=OperatorControlState())
    assert decision.allowed is False
    assert decision.reason == "model_v2_not_enabled_for_runtime_mode"
    assert decision.effective_engine == "heuristic"


def test_not_promoted_model_v2_blocks_without_fallback(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2",
        fallback_to_heuristic=False,
    )
    settings = load_settings(app_cfg, agents_cfg)
    decision = resolve_runtime_model_gate_decision(settings=settings, state=OperatorControlState())
    assert decision.allowed is False
    assert decision.reason == "model_v2_not_enabled_for_runtime_mode"
    assert decision.effective_engine == "model_v2"


def test_promoted_version_mismatch_falls_back(temp_config_paths: tuple[Path, Path], tmp_path: Path) -> None:
    app_cfg, agents_cfg = temp_config_paths
    model_artifact = tmp_path / "model_artifact.json"
    model_artifact.write_text(json.dumps({"model_version": "v2.2.0"}), encoding="utf-8")
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2",
        fallback_to_heuristic=True,
        model_artifact_path=str(model_artifact),
    )
    settings = load_settings(app_cfg, agents_cfg)
    state = OperatorControlState(
        model_v2_promoted=True,
        model_v2_promoted_model_version="v2.1.0",
    )
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    assert decision.allowed is False
    assert decision.reason == "model_v2_not_enabled_for_runtime_mode"
    assert decision.effective_engine == "heuristic"


def test_promoted_version_allows_model_v2_in_sandbox_chain(
    temp_config_paths: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    model_artifact = tmp_path / "model_artifact.json"
    model_artifact.write_text(json.dumps({"model_version": "v2.3.0"}), encoding="utf-8")
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="SANDBOX_CHAIN",
        engine="model_v2",
        fallback_to_heuristic=False,
        model_artifact_path=str(model_artifact),
    )
    settings = load_settings(app_cfg, agents_cfg)
    state = OperatorControlState(
        model_v2_promoted=True,
        model_v2_promoted_model_version="v2.3.0",
    )
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    assert decision.allowed is True
    assert decision.reason == "model_v2_allowed"
    assert decision.effective_engine == "model_v2"


def test_promoted_version_in_paper_live_still_blocked_by_activation_mode(
    temp_config_paths: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    model_artifact = tmp_path / "model_artifact.json"
    model_artifact.write_text(json.dumps({"model_version": "v2.3.0"}), encoding="utf-8")
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2",
        fallback_to_heuristic=True,
        model_artifact_path=str(model_artifact),
    )
    settings = load_settings(app_cfg, agents_cfg)
    state = OperatorControlState(
        model_v2_promoted=True,
        model_v2_promoted_model_version="v2.3.0",
    )
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    assert decision.allowed is False
    assert decision.reason == "model_v2_not_enabled_for_runtime_mode"
    assert decision.effective_engine == "heuristic"


def test_rollback_forces_heuristic_even_without_fallback(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="DRY_RUN_STATIC",
        engine="model_v2",
        fallback_to_heuristic=False,
    )
    settings = load_settings(app_cfg, agents_cfg)
    state = OperatorControlState(model_v2_rollback_active=True, model_v2_rollback_reason="incident")
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    assert decision.allowed is False
    assert decision.reason == "model_v2_rollback_active"
    assert decision.effective_engine == "heuristic"


def test_alt_promoted_engine_alias_is_still_guarded_by_model_v2_gate(
    temp_config_paths: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    model_artifact = tmp_path / "model_artifact.json"
    model_artifact.write_text(json.dumps({"model_version": "v2.5.0"}), encoding="utf-8")
    _configure_prediction_engine(
        app_cfg,
        agents_cfg,
        runtime_mode="PAPER_LIVE",
        engine="model_v2_alt_promoted",
        fallback_to_heuristic=True,
        model_artifact_path=str(model_artifact),
    )
    settings = load_settings(app_cfg, agents_cfg)
    state = OperatorControlState(
        model_v2_promoted=True,
        model_v2_promoted_model_version="v2.5.0",
    )
    decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    assert decision.allowed is False
    assert decision.reason == "model_v2_not_enabled_for_runtime_mode"
    assert decision.effective_engine == "heuristic"
