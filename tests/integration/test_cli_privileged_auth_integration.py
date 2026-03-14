from __future__ import annotations

from pathlib import Path

import yaml

from prediction_market_bot.main import main


def _enable_cli_auth(app_cfg: Path) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("security", {})
    raw["security"]["cli_auth"] = {
        "enabled": True,
        "actor_user_env": "PM_BOT_ACTOR_USER",
        "actor_role_env": "PM_BOT_ACTOR_ROLE",
    }
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_cli_privileged_commands_require_roles_when_cli_auth_enabled(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_cli_auth(app_cfg)

    blocked_missing_identity = main(
        [
            "pause",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--reason",
            "missing-identity",
        ]
    )
    assert blocked_missing_identity == 2

    monkeypatch.setenv("PM_BOT_ACTOR_USER", "operator-user")
    monkeypatch.setenv("PM_BOT_ACTOR_ROLE", "operator")

    blocked_admin_only = main(
        [
            "pause",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--reason",
            "operator-cannot-pause",
        ]
    )
    assert blocked_admin_only == 2

    review_allowed_for_operator = main(
        [
            "review-approve",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            "missing-queue",
            "--operator-id",
            "operator-user",
            "--rationale",
            "operator test",
        ]
    )
    assert review_allowed_for_operator == 1

    tx_resubmit_blocked_for_operator = main(
        [
            "tx-resubmit-safe",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--intent-id",
            "missing-intent",
        ]
    )
    assert tx_resubmit_blocked_for_operator == 2

    db_restore_blocked_for_operator = main(
        [
            "db-restore",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--backup-file",
            str(app_cfg.parent / "missing.sqlite3"),
            "--force",
        ]
    )
    assert db_restore_blocked_for_operator == 2

    monkeypatch.setenv("PM_BOT_ACTOR_ROLE", "admin")

    pause_admin = main(
        [
            "pause",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--reason",
            "admin-can-pause",
        ]
    )
    assert pause_admin == 0

    resume_admin = main(
        [
            "resume",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
        ]
    )
    assert resume_admin == 0
