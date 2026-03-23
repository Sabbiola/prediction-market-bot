from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.main import main


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_model_artifact(
    path: Path,
    *,
    feature_schema_version: str = "v1",
    feature_columns: list[str] | None = None,
    required_features: list[str] | None = None,
) -> None:
    resolved_feature_columns = feature_columns or [
        "f_market_yes_price",
        "f_research_weighted_sentiment",
        "f_market_price_distance_0_5",
    ]
    resolved_required_features = required_features if required_features is not None else list(resolved_feature_columns)
    means = [0.5] * len(resolved_feature_columns)
    stds = [0.25] * len(resolved_feature_columns)
    weights = [0.35] * len(resolved_feature_columns)
    payload = {
        "artifact_type": "prediction_model_v2",
        "artifact_version": "v1",
        "model_name": "logistic_regression_baseline",
        "model_version": "train-202603220001",
        "feature_schema_version": feature_schema_version,
        "feature_columns": resolved_feature_columns,
        "required_features": resolved_required_features,
        "algorithm_payload": {
            "algorithm": "logistic_regression",
            "feature_names": resolved_feature_columns,
            "means": means,
            "stds": stds,
            "weights": weights,
            "bias": 0.05,
        },
        "calibration": {
            "artifact_type": "prediction_calibration_v2",
            "artifact_version": "v1",
            "calibration_version": "cal-202603220001",
            "method": "platt",
            "parameters": {"a": 1.0, "b": 0.0},
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _configure_shadow_engine(
    agents_cfg_path: Path,
    *,
    model_artifact_path: Path,
    alt_shadow_model_artifact_path: Path | None = None,
    alt_shadow_enabled: bool = False,
) -> None:
    payload = yaml.safe_load(agents_cfg_path.read_text(encoding="utf-8")) or {}
    agents = payload.setdefault("agents", {})
    if not isinstance(agents, dict):
        raise AssertionError("agents config must be a mapping")
    prediction = agents.setdefault("prediction", {})
    if not isinstance(prediction, dict):
        raise AssertionError("agents.prediction config must be a mapping")
    inference = prediction.setdefault("model_inference", {})
    if not isinstance(inference, dict):
        raise AssertionError("agents.prediction.model_inference config must be a mapping")
    inference["engine"] = "shadow_scoring"
    inference["model_artifact_path"] = str(model_artifact_path)
    inference["feature_schema_version"] = "v1"
    inference["strict_feature_parity"] = True
    inference["fallback_to_heuristic"] = True
    if alt_shadow_enabled:
        inference["alt_shadow"] = {
            "enabled": True,
            "model_artifact_path": str(alt_shadow_model_artifact_path) if alt_shadow_model_artifact_path else "",
            "feature_schema_version": "alt-v1",
            "strict_feature_parity": True,
            "required_source_coverage": ["news", "reddit", "x"],
            "require_llm_enrichment": True,
        }
    agents_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_cli_run_once_replay_and_generate_report(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    report_path = tmp_path / "reports" / "alpha_report.md"

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

    replay_exit = main(
        [
            "replay-run",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert replay_exit == 0

    report_exit = main(
        [
            "generate-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(report_path),
        ]
    )
    assert report_exit == 0
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert deterministic_run_id in report
    assert "## Summary" in report
    assert "## Artifact Counts" in report
    assert "## Observability" in report

    artifacts_dir = tmp_path / "artifacts"
    required_artifacts = [
        "market_snapshots.jsonl",
        "research_packets.jsonl",
        "prediction_results.jsonl",
        "risk_decisions.jsonl",
        "execution_results.jsonl",
        "settlement_results.jsonl",
        "postmortems.jsonl",
    ]
    for filename in required_artifacts:
        path = artifacts_dir / filename
        assert path.exists()
        rows = _read_jsonl(path)
        assert any(row.get("run_id") == deterministic_run_id for row in rows)


def test_cli_profile_mode_emits_timing_summary(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    run_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            f"{deterministic_run_id}-profile",
            "--profile",
        ]
    )
    assert run_exit == 0
    stderr_text = capsys.readouterr().err
    assert "profile command=run-once" in stderr_text


def test_cli_shadow_scoring_persists_comparison_and_generates_report(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    model_artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(model_artifact)
    _configure_shadow_engine(agents_cfg, model_artifact_path=model_artifact)

    run_id = f"{deterministic_run_id}-shadow"
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

    artifacts_dir = tmp_path / "artifacts"
    shadow_rows = _read_jsonl(artifacts_dir / "prediction_shadow_comparisons.jsonl")
    run_rows = [row for row in shadow_rows if row.get("run_id") == run_id]
    assert run_rows
    first_payload = run_rows[0]["payload"]
    assert isinstance(first_payload, dict)
    assert first_payload.get("primary_prediction") == "heuristic"
    assert isinstance(first_payload.get("heuristic_prediction"), dict)
    assert isinstance(first_payload.get("model_v2_prediction"), dict)

    report_path = tmp_path / "reports" / "shadow.md"
    report_exit = main(
        [
            "generate-shadow-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_id,
            "--output",
            str(report_path),
        ]
    )
    assert report_exit == 0
    report = report_path.read_text(encoding="utf-8")
    assert "## Calibration Deltas" in report
    assert "## Edge Deltas" in report
    assert "## Approval Rate Deltas" in report
    assert "## Disagreement Buckets" in report


def test_cli_shadow_scoring_surfaces_alt_llm_mismatch_warnings(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    primary_artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(primary_artifact)
    alt_artifact = tmp_path / "alt_shadow_model_artifact.json"
    _write_model_artifact(
        alt_artifact,
        feature_schema_version="alt-v1",
        feature_columns=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        required_features=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
    )
    _configure_shadow_engine(
        agents_cfg,
        model_artifact_path=primary_artifact,
        alt_shadow_model_artifact_path=alt_artifact,
        alt_shadow_enabled=True,
    )

    run_id = f"{deterministic_run_id}-shadow-alt-warning"
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

    artifacts_dir = tmp_path / "artifacts"
    shadow_rows = _read_jsonl(artifacts_dir / "prediction_shadow_comparisons.jsonl")
    run_rows = [row for row in shadow_rows if row.get("run_id") == run_id]
    assert run_rows
    first_payload = run_rows[0]["payload"]
    assert isinstance(first_payload, dict)
    assert first_payload.get("parity_status") == "warning"
    assert first_payload.get("alt_llm_shadow_status") in {"inference_error", "available_with_parity_warnings"}
    warnings = first_payload.get("parity_warnings")
    assert isinstance(warnings, list)
    assert any("missing_source_coverage source=news" in warning for warning in warnings)
    assert any("enrichment_pipeline_failure" in warning for warning in warnings)

    report_json_exit = main(
        [
            "generate-shadow-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_id,
            "--json",
        ]
    )
    assert report_json_exit == 0
