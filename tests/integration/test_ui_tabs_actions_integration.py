from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prediction_market_bot.ui.app import create_web_app


def test_ui_tabs_and_operator_actions_end_to_end(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    run_id = f"{deterministic_run_id}-ui"
    second_run_id = f"{deterministic_run_id}-ui-second"

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "Overview" in page.text
        assert "System Health" in page.text
        assert "Scanner" in page.text
        assert "Review Queue" in page.text
        assert "Positions" in page.text
        assert "Reports" in page.text
        assert "Reports" in page.text

        overview_before = client.get("/api/tabs/overview")
        assert overview_before.status_code == 200
        overview_before_payload = overview_before.json()
        assert "runtime_mode" in overview_before_payload
        assert "execution_mode" in overview_before_payload
        assert "live_source_failures_total" in overview_before_payload
        assert "review_queue_depth" in overview_before_payload
        assert "open_positions_count" in overview_before_payload
        assert "pending_settlements_count" in overview_before_payload
        assert "tx_pending_count" in overview_before_payload
        assert "stale_data_events_total" in overview_before_payload
        assert "incident_banners" in overview_before_payload
        assert "incidents_feed" in overview_before_payload
        assert "last_run" in overview_before_payload

        system = client.get("/api/tabs/system")
        assert system.status_code == 200
        system_payload = system.json()
        assert "startup_validation" in system_payload
        assert "healthcheck" in system_payload
        assert "db_connectivity" in system_payload
        assert "provider_status" in system_payload

        pause = client.post("/api/actions/pause", json={"reason": "integration-ui-pause"})
        assert pause.status_code == 200
        assert pause.json()["status"] == "completed"

        blocked_run = client.post("/api/actions/run-once", json={"run_id": f"{run_id}-blocked"})
        assert blocked_run.status_code == 200
        assert blocked_run.json()["status"] == "blocked"

        resume = client.post("/api/actions/resume")
        assert resume.status_code == 200
        assert resume.json()["status"] == "completed"

        run_once = client.post("/api/actions/run-once", json={"run_id": run_id})
        assert run_once.status_code == 200
        run_once_payload = run_once.json()
        assert run_once_payload["status"] == "completed"
        assert run_once_payload["accepted"] is True

        run_once_second = client.post("/api/actions/run-once", json={"run_id": second_run_id})
        assert run_once_second.status_code == 200
        assert run_once_second.json()["status"] == "completed"

        overview_after = client.get("/api/tabs/overview")
        assert overview_after.status_code == 200
        overview_after_payload = overview_after.json()
        assert overview_after_payload["last_run"]["run_id"] == second_run_id

        specific_overview = client.get(f"/api/tabs/overview?run_id={run_id}")
        assert specific_overview.status_code == 200
        specific_overview_payload = specific_overview.json()
        assert specific_overview_payload["run_selector"]["selected_run_id"] == run_id
        assert specific_overview_payload["last_run"]["run_id"] == run_id

        page_for_selected_run = client.get(f"/?run_id={run_id}")
        assert page_for_selected_run.status_code == 200
        assert run_id in page_for_selected_run.text

        tab_paths = (
            "/api/tabs/scanner",
            "/api/tabs/research",
            "/api/tabs/prediction",
            "/api/tabs/risk",
            "/api/tabs/review-queue",
            "/api/tabs/execution",
            "/api/tabs/sandbox-tx",
            "/api/tabs/positions",
            "/api/tabs/settlement",
            "/api/tabs/reports",
        )
        for path in tab_paths:
            response = client.get(f"{path}?run_id={run_id}")
            assert response.status_code == 200
            payload = response.json()
            assert payload["run_selector"]["selected_run_id"] == run_id

        incidents = client.get(f"/api/incidents?run_id={run_id}&limit=20")
        assert incidents.status_code == 200
        incidents_payload = incidents.json()
        assert incidents_payload["run_id"] == run_id
        assert "rows" in incidents_payload

        reports_payload = client.get(f"/api/tabs/reports?run_id={run_id}").json()
        assert "replay_shortcut" in reports_payload
        assert "eval_run_shortcut" in reports_payload
        assert "run_overview_url" in reports_payload
        assert "review_queue_url" in reports_payload
