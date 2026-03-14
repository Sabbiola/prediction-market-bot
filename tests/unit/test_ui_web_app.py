from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prediction_market_bot.ui.app import create_web_app


def test_web_app_bootstrap_and_base_template_render(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "Prediction Market Bot" in response.text
        assert "System/Health" in response.text
        assert "Scanner" in response.text
        assert "Research" in response.text
        assert "Prediction" in response.text
        assert "Risk" in response.text
        assert "Review Queue" in response.text
        assert "Execution" in response.text
        assert "Settlement" in response.text
        assert "Sandbox TX" in response.text
        assert "Reports" in response.text

        tab_paths = (
            "/api/tabs/overview",
            "/api/tabs/system",
            "/api/tabs/scanner",
            "/api/tabs/research",
            "/api/tabs/prediction",
            "/api/tabs/risk",
            "/api/tabs/execution",
            "/api/tabs/settlement",
            "/api/tabs/review-queue",
            "/api/tabs/sandbox-tx",
            "/api/tabs/reports",
        )
        for tab_path in tab_paths:
            tab_response = client.get(tab_path)
            assert tab_response.status_code == 200
            tab_payload = tab_response.json()
            assert "generated_at" in tab_payload
            assert "run_selector" in tab_payload

        overview_payload = client.get("/api/tabs/overview").json()
        assert "runtime_mode" in overview_payload
        assert "review_queue_depth" in overview_payload
        assert "live_source_failures_total" in overview_payload
        assert "stale_data_events_total" in overview_payload
        assert "incident_banners" in overview_payload
        assert "incidents_feed" in overview_payload

        system_payload = client.get("/api/tabs/system").json()
        assert "startup_validation" in system_payload
        assert "db_connectivity" in system_payload

        incidents_payload = client.get("/api/incidents").json()
        assert "generated_at" in incidents_payload
        assert "rows" in incidents_payload


def test_health_and_ready_endpoints(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_payload = health.json()
        assert health_payload["status"] == "ok"
        assert "runtime_mode" in health_payload

        ready = client.get("/ready")
        assert ready.status_code == 200
        ready_payload = ready.json()
        assert ready_payload["status"] == "ready"
        assert ready_payload["ready"] is True
        assert ready_payload["checks"]


def test_operator_actions_use_post_endpoints(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    with TestClient(app) as client:
        assert client.get("/api/actions/run-once").status_code == 405
        assert client.get("/api/actions/review-approve").status_code == 405
        assert client.get("/api/actions/review-reject").status_code == 405
        assert client.get("/api/actions/tx-reconcile").status_code == 405
        assert client.get("/api/actions/tx-resubmit-safe").status_code == 405
        pause = client.post("/api/actions/pause", json={"reason": "unit-test-pause"})
        assert pause.status_code == 200
        assert pause.json()["status"] == "completed"

        blocked = client.post("/api/actions/run-once", json={"run_id": "unit-ui-blocked"})
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"

        resume = client.post("/api/actions/resume")
        assert resume.status_code == 200
        assert resume.json()["status"] == "completed"


def test_shell_stays_up_if_one_panel_fails(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    def _raise_panel_error() -> object:
        raise RuntimeError("forced-panel-failure")

    app.state.read_models.research_tab = _raise_panel_error
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "panel_temporarily_unavailable" in response.text
