from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.main import main
from prediction_market_bot.services import PaperPortfolioEngine


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


def _report_value(report: str, key: str) -> str:
    marker = f"- {key}:"
    for line in report.splitlines():
        text = line.strip()
        if text.startswith(marker):
            return text.split(":", 1)[1].strip()
    return ""


def test_open_positions_state_survives_engine_restart(
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

    artifacts_dir = app_cfg.parent / "artifacts"
    rows = []
    events_path = artifacts_dir / "paper_portfolio_events.jsonl"
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))

    engine_first = PaperPortfolioEngine()
    engine_first.replay_rows(rows)
    first_snapshot = engine_first.snapshot()
    assert first_snapshot.open_position_count >= 1

    engine_second = PaperPortfolioEngine()
    engine_second.replay_rows(rows)
    second_snapshot = engine_second.snapshot()
    assert second_snapshot.open_position_count == first_snapshot.open_position_count
    assert second_snapshot.total_exposure_usd == first_snapshot.total_exposure_usd


def test_reports_show_open_vs_settled_state(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
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

    report_open_path = tmp_path / "open.md"
    report_open_exit = main(
        [
            "generate-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(report_open_path),
        ]
    )
    assert report_open_exit == 0
    report_open = report_open_path.read_text(encoding="utf-8")
    assert "settlement_queue" in report_open
    assert _report_value(report_open, "pending") != ""
    assert _report_value(report_open, "open_positions") != ""

    settle_exit = main(
        [
            "run-settlement-lane",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert settle_exit == 0

    report_settled_path = tmp_path / "settled.md"
    report_settled_exit = main(
        [
            "generate-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(report_settled_path),
        ]
    )
    assert report_settled_exit == 0
    report_settled = report_settled_path.read_text(encoding="utf-8")
    assert _report_value(report_settled, "pending") == "0"
    assert _report_value(report_settled, "open_positions") == "0"
