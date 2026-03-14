from __future__ import annotations

import hashlib
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from prediction_market_bot.ui.app import create_web_app


def _enable_ui_auth(app_cfg: Path, *, timeout_sec: int = 1800) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw["ui_auth"] = {
        "enabled": True,
        "session_secret_env": "PM_BOT_UI_SESSION_SECRET",
        "session_timeout_sec": timeout_sec,
        "cookie_name": "pm_bot_ui_session",
        "cookie_secure": False,
        "cookie_samesite": "lax",
        "users": [
            {"username": "viewer_user", "role": "viewer", "password": "viewer-pass"},
            {"username": "operator_user", "role": "operator", "password": "operator-pass"},
            {"username": "admin_user", "role": "admin", "password": "admin-pass"},
        ],
    }
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def _pbkdf2_hash(password: str) -> str:
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 150_000)
    return f"pbkdf2_sha256$150000${salt.hex()}${digest.hex()}"


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True


def test_ui_auth_requires_login_for_tabs_when_enabled(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        page = client.get("/", follow_redirects=False)
        assert page.status_code in {302, 303, 307}
        assert "/login" in page.headers.get("location", "")

        tabs = client.get("/api/tabs/overview")
        assert tabs.status_code == 401
        incidents = client.get("/api/incidents")
        assert incidents.status_code == 401


def test_ui_rbac_viewer_is_read_only(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "viewer_user", "viewer-pass")

        tab = client.get("/api/tabs/overview")
        assert tab.status_code == 200
        incidents = client.get("/api/incidents")
        assert incidents.status_code == 200

        blocked_run = client.post("/api/actions/run-once", json={"run_id": "viewer-blocked"})
        assert blocked_run.status_code == 403

        blocked_pause = client.post("/api/actions/pause", json={"reason": "viewer-forbidden"})
        assert blocked_pause.status_code == 403


def test_ui_rbac_operator_can_run_once_but_not_pause(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "operator_user", "operator-pass")

        run_once = client.post("/api/actions/run-once", json={"run_id": f"{deterministic_run_id}-rbac-op"})
        assert run_once.status_code == 200
        assert run_once.json()["acting_user"] == "operator_user"
        assert run_once.json()["acting_role"] == "operator"

        reconcile = client.post("/api/actions/tx-reconcile", json={"run_id": "", "intent_id": "", "limit": 5})
        assert reconcile.status_code == 200

        blocked_pause = client.post("/api/actions/pause", json={"reason": "operator-forbidden"})
        assert blocked_pause.status_code == 403



def test_ui_rbac_admin_can_pause_resume_and_admin_settings(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "admin_user", "admin-pass")

        pause = client.post("/api/actions/pause", json={"reason": "admin-maintenance"})
        assert pause.status_code == 200

        resume = client.post("/api/actions/resume")
        assert resume.status_code == 200

        admin_setting = client.post(
            "/api/actions/admin-settings",
            json={"setting": "maintenance_note", "value": "scheduled test", "confirm": True},
        )
        assert admin_setting.status_code == 200
        admin_payload = admin_setting.json()
        assert admin_payload["status"] == "completed"
        assert admin_payload["acting_role"] == "admin"


def test_ui_auth_logout_and_session_timeout(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg, timeout_sec=1)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "viewer_user", "viewer-pass")

        first = client.get("/api/tabs/overview")
        assert first.status_code == 200

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200

        after_logout = client.get("/api/tabs/overview")
        assert after_logout.status_code == 401

        _login(client, "viewer_user", "viewer-pass")
        time.sleep(1.2)
        expired = client.get("/api/tabs/overview")
        assert expired.status_code == 401


def test_ui_auth_hash_mode_fails_closed_on_plaintext_config(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("ui_auth", {})
    raw["ui_auth"]["require_password_hashes"] = True
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    try:
        create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    except ValueError as exc:
        assert "must provide a password hash" in str(exc)
    else:
        raise AssertionError("Expected create_web_app to fail on plaintext credentials in hash-only mode")


def test_ui_auth_hash_mode_accepts_pbkdf2_credentials_and_lockout(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw["ui_auth"] = {
        "enabled": True,
        "session_secret_env": "PM_BOT_UI_SESSION_SECRET",
        "session_timeout_sec": 1800,
        "cookie_name": "pm_bot_ui_session",
        "cookie_secure": False,
        "cookie_samesite": "lax",
        "require_password_hashes": True,
        "max_failed_attempts": 2,
        "lockout_seconds": 1,
        "users": [
            {
                "username": "operator_hash",
                "role": "operator",
                "password_hash": _pbkdf2_hash("operator-pass"),
            }
        ],
    }
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        for _ in range(2):
            bad = client.post("/api/auth/login", json={"username": "operator_hash", "password": "bad-pass"})
            assert bad.status_code == 200
            assert bad.json()["authenticated"] is False
        locked = client.post("/api/auth/login", json={"username": "operator_hash", "password": "operator-pass"})
        assert locked.status_code == 200
        assert locked.json()["authenticated"] is False
        time.sleep(1.2)
        good = client.post("/api/auth/login", json={"username": "operator_hash", "password": "operator-pass"})
        assert good.status_code == 200
        assert good.json()["authenticated"] is True
