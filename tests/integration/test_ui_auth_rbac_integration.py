from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult, RiskDecision
from prediction_market_bot.services import TradeReviewQueueService
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


def _button_exists(page_text: str, button_id: str) -> bool:
    pattern = rf'<button[^>]*id="{re.escape(button_id)}"[^>]*>'
    return re.search(pattern, page_text) is not None


def _button_disabled(page_text: str, button_id: str) -> bool:
    pattern = rf'<button[^>]*id="{re.escape(button_id)}"[^>]*disabled[^>]*>'
    return re.search(pattern, page_text) is not None


def _seed_review_candidate_for_ui(temp_config_paths: tuple[Path, Path], run_id: str) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    candidate = MarketCandidate(
        market=MarketSnapshot.from_yes_price(
            market_id="ui-auth-review-market-1",
            venue="polymarket",
            title="Will review RBAC controls render correctly?",
            yes_price=0.45,
            liquidity_usd=50_000,
            volume_24h_usd=30_000,
            spread_bps=95,
            hours_to_resolution=24,
            last_price_move_bps=15,
            category="test",
        ),
        scan_score=0.8,
        reasons=("seed",),
    )
    prediction = PredictionResult(
        market_id="ui-auth-review-market-1",
        selected_side=OutcomeSide.YES,
        market_yes_prob=0.45,
        fair_yes_prob=0.62,
        selected_market_price=0.45,
        selected_fair_price=0.62,
        edge=0.17,
        confidence=0.81,
        rationale=("auth_review_prediction_context",),
    )
    risk = RiskDecision(
        market_id="ui-auth-review-market-1",
        approved=True,
        side=OutcomeSide.YES,
        stake_usd=95.0,
        bankroll_fraction=0.01,
        fractional_kelly=0.02,
        max_loss_usd=95.0,
        reasoning=("auth_review_risk_context",),
    )
    queue.enqueue_candidate(run_id, candidate, prediction, risk)


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

        blocked_review = client.post(
            "/api/actions/review-approve",
            json={
                "queue_id": "viewer-queue",
                "operator_id": "viewer_user",
                "rationale": "viewer should not approve",
                "note": "",
                "confirm": True,
            },
        )
        assert blocked_review.status_code == 403


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

        review_attempt = client.post(
            "/api/actions/review-approve",
            json={
                "queue_id": "operator-missing-queue",
                "operator_id": "operator_user",
                "rationale": "operator endpoint access check",
                "note": "",
                "confirm": True,
            },
        )
        assert review_attempt.status_code == 200
        review_payload = review_attempt.json()
        assert review_payload["acting_user"] == "operator_user"
        assert review_payload["acting_role"] == "operator"

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


def test_ui_login_page_and_error_feedback(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert "Sign in to access role-based controls" in login_page.text
        assert "Viewer:</strong> observe all tabs, no write actions." in login_page.text
        assert "Operator:</strong> viewer access plus review and tx reconcile actions." in login_page.text
        assert "Admin:</strong> operator access plus pause/resume and admin settings." in login_page.text

        bad_login = client.post(
            "/login",
            data={"username": "viewer_user", "password": "wrong", "next_path": "/"},
        )
        assert bad_login.status_code == 401
        assert "Login failed" in bad_login.text
        assert "Invalid username or password. Check credentials and try again." in bad_login.text

        good_login_external_next = client.post(
            "/login",
            data={"username": "viewer_user", "password": "viewer-pass", "next_path": "https://example.com"},
            follow_redirects=False,
        )
        assert good_login_external_next.status_code in {302, 303, 307}
        assert good_login_external_next.headers.get("location", "") == "/"


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


def test_ui_overview_quick_actions_are_role_aware(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "viewer_user", "viewer-pass")
        viewer_page = client.get("/")
        assert viewer_page.status_code == 200
        assert "Go To Pending Review" in viewer_page.text
        assert _button_exists(viewer_page.text, "action-run-once")
        assert _button_exists(viewer_page.text, "action-pause")
        assert _button_exists(viewer_page.text, "action-resume")
        assert _button_disabled(viewer_page.text, "action-run-once")
        assert _button_disabled(viewer_page.text, "action-pause")
        assert _button_disabled(viewer_page.text, "action-resume")
        assert "Run Once is unavailable for role" in viewer_page.text
        assert "Pause/Resume is unavailable for role" in viewer_page.text
        assert "session_expires=" in viewer_page.text
        assert "access=observe_only" in viewer_page.text

        client.post("/api/auth/logout")
        _login(client, "operator_user", "operator-pass")
        operator_page = client.get("/")
        assert operator_page.status_code == 200
        assert _button_exists(operator_page.text, "action-run-once")
        assert _button_exists(operator_page.text, "action-pause")
        assert _button_exists(operator_page.text, "action-resume")
        assert not _button_disabled(operator_page.text, "action-run-once")
        assert _button_disabled(operator_page.text, "action-pause")
        assert _button_disabled(operator_page.text, "action-resume")
        assert "Pause/Resume is unavailable for role" in operator_page.text

        client.post("/api/auth/logout")
        _login(client, "admin_user", "admin-pass")
        admin_page = client.get("/")
        assert admin_page.status_code == 200
        assert _button_exists(admin_page.text, "action-run-once")
        assert _button_exists(admin_page.text, "action-pause")
        assert _button_exists(admin_page.text, "action-resume")
        assert not _button_disabled(admin_page.text, "action-run-once")
        assert not _button_disabled(admin_page.text, "action-pause")
        assert not _button_disabled(admin_page.text, "action-resume")


def test_ui_review_workflow_controls_are_role_aware(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "test-session-secret-123456")
    run_id = f"{deterministic_run_id}-ui-rbac-review"
    _seed_review_candidate_for_ui(temp_config_paths, run_id)

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    with TestClient(app) as client:
        _login(client, "viewer_user", "viewer-pass")
        viewer_page = client.get(f"/?run_id={run_id}&active_tab=review-queue&review_status=PENDING_REVIEW")
        assert viewer_page.status_code == 200
        assert "Current role is read-only. Review approve/reject requires operator or admin role." in viewer_page.text
        assert _button_exists(viewer_page.text, "action-review-approve")
        assert _button_exists(viewer_page.text, "action-review-reject")
        assert _button_disabled(viewer_page.text, "action-review-approve")
        assert _button_disabled(viewer_page.text, "action-review-reject")

        client.post("/api/auth/logout")
        _login(client, "operator_user", "operator-pass")
        operator_page = client.get(f"/?run_id={run_id}&active_tab=review-queue&review_status=PENDING_REVIEW")
        assert operator_page.status_code == 200
        assert _button_exists(operator_page.text, "action-review-approve")
        assert _button_exists(operator_page.text, "action-review-reject")
        assert not _button_disabled(operator_page.text, "action-review-approve")
        assert not _button_disabled(operator_page.text, "action-review-reject")
