from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.main import main


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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
