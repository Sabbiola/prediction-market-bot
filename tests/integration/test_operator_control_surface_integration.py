from __future__ import annotations

from pathlib import Path

from prediction_market_bot.main import main


def test_operator_control_surface_commands(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    tmp_path: Path,
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
            "integration-test-pause",
        ]
    )
    assert pause_exit == 0

    blocked_run_exit = main(
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
    assert blocked_run_exit == 2

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

    status_exit = main(
        [
            "status",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
        ]
    )
    assert status_exit == 0

    replay_exit = main(
        [
            "replay",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert replay_exit == 0

    portfolio_exit = main(
        [
            "portfolio",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert portfolio_exit == 0

    scheduler_exit = main(
        [
            "run-scheduler",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--interval-sec",
            "0",
            "--max-iterations",
            "1",
            "--run-id-prefix",
            "it-sched",
        ]
    )
    assert scheduler_exit == 0

    last_report_json_exit = main(
        [
            "last-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--format",
            "json",
            "--path-only",
        ]
    )
    assert last_report_json_exit == 0

    reports_dir = tmp_path / "artifacts" / "reports"
    assert (reports_dir / f"{deterministic_run_id}.md").exists()
    assert (reports_dir / f"{deterministic_run_id}.json").exists()
    assert (reports_dir / "latest.md").exists()
    assert (reports_dir / "latest.json").exists()
