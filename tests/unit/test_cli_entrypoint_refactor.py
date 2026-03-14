from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot import main as legacy_main
from prediction_market_bot.cli import app as cli_app
from prediction_market_bot.cli import parser as cli_parser


def test_legacy_main_exports_cli_entrypoint() -> None:
    assert legacy_main.main is cli_app.main
    assert legacy_main.build_parser is cli_parser.build_parser


def test_run_once_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_once_command(
        config_path: Path,
        agents_config_path: Path,
        run_id: str | None = None,
        *,
        force: bool = False,
        profile: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["force"] = force
        captured["profile"] = profile
        return 7

    monkeypatch.setattr(cli_app, "run_once_command", fake_run_once_command)

    exit_code = cli_app.main(
        [
            "run-once",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "run-123",
            "--force",
        ]
    )

    assert exit_code == 7
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "run_id": "run-123",
        "force": True,
        "profile": False,
    }


def test_run_once_dispatch_profile_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_once_command(
        config_path: Path,
        agents_config_path: Path,
        run_id: str | None = None,
        *,
        force: bool = False,
        profile: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["force"] = force
        captured["profile"] = profile
        return 0

    monkeypatch.setattr(cli_app, "run_once_command", fake_run_once_command)

    exit_code = cli_app.main(
        [
            "run-once",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "run-prof",
            "--profile",
        ]
    )

    assert exit_code == 0
    assert captured["profile"] is True


def test_generate_eval_report_requires_scope() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_app.main(
            [
                "generate-eval-report",
                "--config",
                "config/app.yaml",
                "--agents-config",
                "config/agents.yaml",
            ]
        )
    assert exc_info.value.code == 2


def test_db_backup_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_db_backup_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        backup_dir: Path | None = None,
        label: str = "",
        as_json: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["backup_dir"] = backup_dir
        captured["label"] = label
        captured["as_json"] = as_json
        return 11

    monkeypatch.setattr(cli_app, "db_backup_command", fake_db_backup_command)

    exit_code = cli_app.main(
        [
            "db-backup",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--backup-dir",
            "data/backups",
            "--label",
            "nightly",
            "--json",
        ]
    )

    assert exit_code == 11
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "backup_dir": Path("data/backups"),
        "label": "nightly",
        "as_json": True,
    }
