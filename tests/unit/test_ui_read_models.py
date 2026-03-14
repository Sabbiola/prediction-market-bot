from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot.main import run_once_command
from prediction_market_bot.ui import read_models as ui_read_models_module
from prediction_market_bot.ui.read_models import UiReadModelService, build_ui_runtime_context


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
    settlement = service.settlement_tab()
    sandbox_tx = service.sandbox_tx_tab()
    reports = service.reports_tab()

    for tab in (scanner, research, prediction, risk, execution, settlement, sandbox_tx, reports):
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
    settlement = service.settlement_tab(run_id=run_id)
    sandbox_tx = service.sandbox_tx_tab(run_id=run_id)
    reports = service.reports_tab(run_id=run_id)

    for tab in (scanner, research, prediction, risk, execution, settlement, sandbox_tx, reports):
        assert tab.run_selector.selected_run_id == run_id
        assert isinstance(tab.generated_at, str)
    assert review_queue.run_selector.selected_run_id == run_id
    assert isinstance(review_queue.generated_at, str)

    assert reports.run_id == run_id
    assert run_id in reports.replay_shortcut.command
    assert run_id in reports.generate_report_shortcut.command
    assert isinstance(reports.incidents_feed, tuple)

    incidents = service.incidents_feed(run_id=run_id, limit=10)
    assert incidents.run_id == run_id
    assert isinstance(incidents.rows, tuple)


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
