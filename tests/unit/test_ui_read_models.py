from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from prediction_market_bot.main import run_once_command
from prediction_market_bot.ui import read_models as ui_read_models_module
from prediction_market_bot.ui.read_models import UiReadModelService, build_ui_runtime_context


def test_ui_read_models_split_package_modules_are_importable() -> None:
    expected_modules = (
        "context",
        "queries",
        "shared",
        "overview",
        "system",
        "scanner",
        "research",
        "prediction",
        "risk",
        "review_queue",
        "execution",
        "positions",
        "settlement",
        "sandbox_tx",
        "reports",
        "service",
    )
    for module_name in expected_modules:
        module = importlib.import_module(f"prediction_market_bot.ui.read_models.{module_name}")
        assert module is not None


def test_ui_read_models_empty_states_without_runs(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    context = build_ui_runtime_context(str(app_cfg), str(agents_cfg))
    service = UiReadModelService(context)

    selector = service.run_selector()
    assert selector.available is False
    assert selector.selected_run_id == ""

    overview = service.overview_tab()
    assert overview.last_run.available is False
    assert isinstance(overview.incident_banners, tuple)
    assert isinstance(overview.incidents_feed, tuple)

    scanner = service.scanner_tab()
    research = service.research_tab()
    prediction = service.prediction_tab()
    risk = service.risk_tab()
    review_queue = service.review_queue_tab()
    execution = service.execution_tab()
    positions = service.positions_tab()
    settlement = service.settlement_tab()
    sandbox_tx = service.sandbox_tx_tab()
    reports = service.reports_tab()

    for tab in (scanner, research, prediction, risk, execution, positions, settlement, sandbox_tx, reports):
        assert tab.run_selector.available is False
    assert review_queue.available is False
    assert review_queue.run_selector.available is False
    assert reports.replay_shortcut.command
    assert reports.eval_window_shortcut.command


def test_ui_read_models_support_run_selection_for_engine_tabs(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    run_id = f"{deterministic_run_id}-ui-read-model"

    exit_code = run_once_command(config_path=app_cfg, agents_config_path=agents_cfg, run_id=run_id)
    assert exit_code == 0

    context = build_ui_runtime_context(str(app_cfg), str(agents_cfg))
    service = UiReadModelService(context)

    overview = service.overview_tab(run_id=run_id)
    assert overview.run_selector.selected_run_id == run_id
    assert overview.last_run.run_id == run_id
    assert "system_nominal" in {item.code for item in overview.incident_banners} or overview.incident_banners

    scanner = service.scanner_tab(run_id=run_id)
    research = service.research_tab(run_id=run_id)
    prediction = service.prediction_tab(run_id=run_id)
    risk = service.risk_tab(run_id=run_id)
    review_queue = service.review_queue_tab(run_id=run_id)
    execution = service.execution_tab(run_id=run_id)
    positions = service.positions_tab(run_id=run_id)
    settlement = service.settlement_tab(run_id=run_id)
    sandbox_tx = service.sandbox_tx_tab(run_id=run_id)
    reports = service.reports_tab(run_id=run_id)

    for tab in (scanner, research, prediction, risk, execution, positions, settlement, sandbox_tx, reports):
        assert tab.run_selector.selected_run_id == run_id
        assert isinstance(tab.generated_at, str)
    assert scanner.panel_status
    assert isinstance(scanner.funnel_summary, tuple)
    assert isinstance(scanner.rejected_reasons, tuple)
    assert isinstance(scanner.market_context_summary, tuple)
    assert isinstance(scanner.diagnostics_summary, tuple)
    assert isinstance(scanner.anomalies, tuple)

    assert research.panel_status
    assert isinstance(research.coverage_summary, tuple)
    assert isinstance(research.source_type_distribution, tuple)
    assert isinstance(research.diagnostics_summary, tuple)
    assert isinstance(research.anomalies, tuple)

    assert prediction.panel_status
    assert isinstance(prediction.avg_probability_gap, float)
    assert isinstance(prediction.parity_warning_count, int)
    assert isinstance(prediction.diagnostics_summary, tuple)
    assert isinstance(prediction.anomalies, tuple)

    assert risk.panel_status
    assert isinstance(risk.proposed_stake_usd, float)
    assert isinstance(risk.approved_stake_usd, float)
    assert isinstance(risk.avg_portfolio_exposure_usd, float)
    assert isinstance(risk.avg_market_exposure_usd, float)
    assert isinstance(risk.daily_stop_triggered, bool)
    assert isinstance(risk.circuit_breaker_active, bool)
    assert isinstance(risk.guardrail_distribution, tuple)
    assert isinstance(risk.diagnostics_summary, tuple)
    assert isinstance(risk.anomalies, tuple)
    assert review_queue.run_selector.selected_run_id == run_id
    assert isinstance(review_queue.generated_at, str)
    assert isinstance(review_queue.executed_count, int)
    assert isinstance(review_queue.lifecycle_distribution, tuple)
    if review_queue.rows:
        sample_row = review_queue.rows[0]
        assert isinstance(sample_row.lifecycle_state, str)
        assert isinstance(sample_row.evidence_coverage_summary, str)
        assert isinstance(sample_row.prediction_url, str)
        assert isinstance(sample_row.risk_url, str)
        assert isinstance(sample_row.sandbox_tx_url, str)
        assert isinstance(sample_row.position_url, str)
    if review_queue.selected_item is not None:
        selected = review_queue.selected_item
        assert isinstance(selected.lifecycle_state, str)
        assert isinstance(selected.evidence_coverage_summary, str)
        assert isinstance(selected.prediction_url, str)
        assert isinstance(selected.risk_url, str)
        assert isinstance(selected.sandbox_tx_url, str)
        assert isinstance(selected.position_url, str)

    assert isinstance(positions.open_positions_count, int)
    assert isinstance(positions.exposure_distribution, tuple)
    if positions.rows:
        sample_position = positions.rows[0]
        assert isinstance(sample_position.settlement_url, str)
        assert isinstance(sample_position.sandbox_tx_url, str)

    assert reports.run_id == run_id
    assert run_id in reports.replay_shortcut.command
    assert run_id in reports.generate_report_shortcut.command
    assert f"run_id={run_id}" in reports.run_overview_url
    assert "active_tab=review-queue" in reports.review_queue_url
    assert "active_tab=sandbox-tx" in reports.sandbox_tx_url
    assert "active_tab=positions" in reports.positions_url
    assert "active_tab=settlement" in reports.settlement_url
    assert isinstance(reports.incidents_feed, tuple)
    if reports.incidents_feed:
        sample_event = reports.incidents_feed[0]
        assert isinstance(sample_event.component, str)
        assert isinstance(sample_event.affected_target, str)
        assert isinstance(sample_event.reason_code, str)
        assert isinstance(sample_event.run_url, str)
        assert isinstance(sample_event.review_queue_url, str)
        assert isinstance(sample_event.sandbox_tx_url, str)
        assert isinstance(sample_event.position_url, str)
        assert isinstance(sample_event.settlement_url, str)
        assert isinstance(sample_event.reports_url, str)

    incidents = service.incidents_feed(run_id=run_id, limit=10)
    assert incidents.run_id == run_id
    assert isinstance(incidents.rows, tuple)
    if incidents.rows:
        sample_event = incidents.rows[0]
        assert isinstance(sample_event.component, str)
        assert isinstance(sample_event.affected_target, str)
        assert isinstance(sample_event.linked_run_id, str)


def test_ui_overview_poll_cache_reuses_recent_payload(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    context = build_ui_runtime_context(str(app_cfg), str(agents_cfg))
    service = UiReadModelService(context)

    call_counter = {"count": 0}
    real_collect = ui_read_models_module.collect_runtime_metrics

    def _counted_collect(**kwargs: object):
        call_counter["count"] += 1
        return real_collect(**kwargs)

    monkeypatch.setattr(ui_read_models_module, "collect_runtime_metrics", _counted_collect)

    _ = service.overview_tab()
    _ = service.overview_tab()
    assert call_counter["count"] == 1


def test_ui_query_cache_reuses_incident_event_scan_and_clear_cache(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    context = build_ui_runtime_context(str(app_cfg), str(agents_cfg))
    service = UiReadModelService(context)

    call_counter = {"count": 0}
    real_read = context.persistence.read_all_run_events

    def _counted_read_all_run_events() -> list[dict[str, object]]:
        call_counter["count"] += 1
        return real_read()

    monkeypatch.setattr(context.persistence, "read_all_run_events", _counted_read_all_run_events)

    _ = service._queries.incident_events(run_id=None, limit=20)
    _ = service._queries.incident_events(run_id=None, limit=20)
    assert call_counter["count"] == 1

    service._queries.clear_cache()
    _ = service._queries.incident_events(run_id=None, limit=20)
    assert call_counter["count"] == 2


def test_ui_query_cache_reuses_artifact_reads_and_is_invalidated_by_service(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    context = build_ui_runtime_context(str(app_cfg), str(agents_cfg))
    service = UiReadModelService(context)

    run_id = "ui-query-cache-run"
    artifact_type = "prediction_results"
    context.persistence.write_artifact(
        run_id,
        artifact_type,
        {
            "market_id": "m-cache",
            "selected_side": "YES",
            "market_yes_prob": 0.4,
            "fair_yes_prob": 0.5,
            "edge": 0.1,
            "confidence": 0.7,
        },
    )

    call_counter = {"count": 0}
    real_read = context.persistence.read_artifact_records

    def _counted_read_artifact_records(selected_run_id: str, selected_artifact_type: str) -> list[dict[str, object]]:
        call_counter["count"] += 1
        return real_read(selected_run_id, selected_artifact_type)

    monkeypatch.setattr(context.persistence, "read_artifact_records", _counted_read_artifact_records)

    first = service._queries.artifact_payloads(run_id, artifact_type)
    second = service._queries.artifact_payloads(run_id, artifact_type)
    assert first and second
    assert call_counter["count"] == 1

    service.invalidate_poll_cache()
    third = service._queries.artifact_payloads(run_id, artifact_type)
    assert third
    assert call_counter["count"] == 2
