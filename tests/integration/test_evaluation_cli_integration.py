from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.main import main


def test_cli_evaluate_window_and_generate_eval_report(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    run_eval_report_path = tmp_path / "reports" / "run_eval.md"
    window_eval_report_path = tmp_path / "reports" / "window_eval.md"

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

    eval_window_exit = main(
        [
            "evaluate-window",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--limit-runs",
            "10",
        ]
    )
    assert eval_window_exit == 0

    run_report_exit = main(
        [
            "generate-eval-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(run_eval_report_path),
        ]
    )
    assert run_report_exit == 0
    assert run_eval_report_path.exists()
    run_report = run_eval_report_path.read_text(encoding="utf-8")
    assert "## Calibration Metrics" in run_report
    assert "## Brier Metrics" in run_report
    assert "## Failure Categories" in run_report

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    window_report_exit = main(
        [
            "generate-eval-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--date-from",
            today,
            "--date-to",
            today,
            "--output",
            str(window_eval_report_path),
        ]
    )
    assert window_report_exit == 0
    assert window_eval_report_path.exists()
    window_report = window_eval_report_path.read_text(encoding="utf-8")
    assert "# Evaluation Report: Window" in window_report
    assert "## Calibration Metrics" in window_report
