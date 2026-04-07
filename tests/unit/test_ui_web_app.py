from __future__ import annotations

from pathlib import Path

import pytest
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
        assert "Review Queue" in response.text
        assert "Reports" in response.text
        assert "System Health" in response.text
        assert "Scanner" in response.text
        assert "Research" in response.text
        assert "Prediction" in response.text
        assert "Risk" in response.text
        assert "Review Queue" in response.text
        assert "Execution" in response.text
        assert "Positions" in response.text
        assert "Settlement" in response.text
        assert "Sandbox TX" in response.text
        assert "Reports" in response.text
        assert "sidebar__nav-item" in response.text
        assert "sidebar__nav-label" in response.text

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
            "/api/tabs/positions",
            "/api/tabs/reports",
        )
        for tab_path in tab_paths:
            tab_response = client.get(tab_path)
            assert tab_response.status_code == 200
            tab_payload = tab_response.json()
            assert "generated_at" in tab_payload
            assert "run_selector" in tab_payload
            assert "x-request-duration-ms" in tab_response.headers
            assert "x-poll-suggested-interval-ms" in tab_response.headers
            assert "cache-control" in tab_response.headers

        overview_payload = client.get("/api/tabs/overview").json()
        assert "runtime_mode" in overview_payload
        assert "review_queue_depth" in overview_payload
        assert "live_source_failures_total" in overview_payload
        assert "stale_data_events_total" in overview_payload
        assert "model_visibility" in overview_payload
        assert "drift_alert" in overview_payload
        assert "incident_banners" in overview_payload
        assert "incidents_feed" in overview_payload

        prediction_payload = client.get("/api/tabs/prediction").json()
        assert "model_visibility" in prediction_payload
        assert "calibration_summary" in prediction_payload
        assert "shadow_history" in prediction_payload
        assert "approval_rate_summary" in prediction_payload
        assert "disagreement_buckets" in prediction_payload
        assert "enrichment_coverage" in prediction_payload
        assert "disagreement_vs_baseline" in prediction_payload
        assert "drift_alert" in prediction_payload

        system_payload = client.get("/api/tabs/system").json()
        assert "overall_status" in system_payload
        assert "startup_validation" in system_payload
        assert "db_connectivity" in system_payload
        assert "review_queue_depth" in system_payload
        assert "tx_pending_count" in system_payload
        assert "pending_settlements_count" in system_payload
        assert "incident_banners" in system_payload
        assert "incidents_feed" in system_payload

        incidents_payload = client.get("/api/incidents").json()
        assert "generated_at" in incidents_payload
        assert "rows" in incidents_payload
        incidents_headers = client.get("/api/incidents").headers
        assert "x-request-duration-ms" in incidents_headers
        assert "x-poll-suggested-interval-ms" in incidents_headers

        scanner_payload = client.get("/api/tabs/scanner").json()
        assert "panel_status" in scanner_payload
        assert "funnel_summary" in scanner_payload
        assert "rejected_reasons" in scanner_payload
        assert "market_context_summary" in scanner_payload
        assert "diagnostics_summary" in scanner_payload
        assert "anomalies" in scanner_payload

        research_payload = client.get("/api/tabs/research").json()
        assert "panel_status" in research_payload
        assert "source_failures_count" in research_payload
        assert "coverage_summary" in research_payload
        assert "diagnostics_summary" in research_payload
        assert "anomalies" in research_payload

        risk_payload = client.get("/api/tabs/risk").json()
        assert "panel_status" in risk_payload
        assert "proposed_stake_usd" in risk_payload
        assert "approved_stake_usd" in risk_payload
        assert "avg_portfolio_exposure_usd" in risk_payload
        assert "avg_market_exposure_usd" in risk_payload
        assert "daily_stop_triggered" in risk_payload
        assert "circuit_breaker_active" in risk_payload
        assert "guardrail_distribution" in risk_payload
        assert "diagnostics_summary" in risk_payload
        assert "anomalies" in risk_payload

        assert "avg_probability_gap" in prediction_payload
        assert "parity_warning_count" in prediction_payload
        assert "diagnostics_summary" in prediction_payload
        assert "anomalies" in prediction_payload

        review_queue_payload = client.get("/api/tabs/review-queue").json()
        assert "executed_count" in review_queue_payload
        assert "lifecycle_distribution" in review_queue_payload
        assert "rows" in review_queue_payload
        assert "selected_item" in review_queue_payload

        positions_payload = client.get("/api/tabs/positions").json()
        assert "open_positions_count" in positions_payload
        assert "total_exposure_usd" in positions_payload
        assert "rows" in positions_payload

        reports_payload = client.get("/api/tabs/reports").json()
        assert "run_overview_url" in reports_payload
        assert "review_queue_url" in reports_payload
        assert "sandbox_tx_url" in reports_payload
        assert "positions_url" in reports_payload
        assert "settlement_url" in reports_payload


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


def test_prediction_tab_includes_alt_promoted_comparison_summary(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    run_id = "ui-alt-promoted-run"
    app.state.read_models._context.persistence.write_artifact(
        run_id,
        "prediction_results",
        {
            "market_id": "m-ui-alt",
            "selected_side": "YES",
            "market_yes_prob": 0.48,
            "fair_yes_prob": 0.55,
            "edge": 0.07,
            "confidence": 0.81,
            "rationale": ["prediction_engine=alt_llm_promoted"],
        },
    )
    app.state.read_models._context.persistence.write_artifact(
        run_id,
        "prediction_alt_comparisons",
        {
            "market_id": "m-ui-alt",
            "comparison_kind": "alt_promoted_vs_model_v2_baseline",
            "disagreement_bucket": "moderate_drift",
            "enrichment_coverage": 0.75,
            "approvals": {
                "model_v2": True,
                "alt_llm_promoted": False,
            },
        },
    )

    with TestClient(app) as client:
        payload = client.get(f"/api/tabs/prediction?run_id={run_id}").json()
        assert payload["available"] is True
        assert payload["enrichment_coverage"] == pytest.approx(0.75, abs=1e-8)
        labels = {item["label"] for item in payload["disagreement_vs_baseline"]}
        assert "moderate_drift" in labels


def test_poll_suggested_interval_headers_use_endpoint_tiers(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
    base_interval_ms = int(round(app.state.ui_context.settings.performance.ui_poll_min_interval_sec * 1000.0))

    with TestClient(app) as client:
        overview_headers = client.get("/api/tabs/overview").headers
        prediction_headers = client.get("/api/tabs/prediction").headers
        reports_headers = client.get("/api/tabs/reports").headers

        overview_interval_ms = int(overview_headers["x-poll-suggested-interval-ms"])
        prediction_interval_ms = int(prediction_headers["x-poll-suggested-interval-ms"])
        reports_interval_ms = int(reports_headers["x-poll-suggested-interval-ms"])

        assert overview_interval_ms == base_interval_ms
        assert prediction_interval_ms == base_interval_ms * 2
        assert reports_interval_ms == base_interval_ms * 3
