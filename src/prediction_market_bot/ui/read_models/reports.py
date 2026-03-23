from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.services import load_operator_state, operator_state_path, replay_run
from prediction_market_bot.ui.models import ReportsTabResponse, RunSelectorResponse

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import chart_from_mapping, empty_reports, incident_links, report_shortcuts


def build_reports_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> ReportsTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_reports(
            selector,
            "no_run_selected",
            config_path=context.config_path,
            agents_config_path=context.agents_config_path,
        )
    summary = replay_run(context.persistence, run)
    available = not (summary.event_count == 0 and summary.total_markets == 0 and summary.candidates == 0)
    state = load_operator_state(
        operator_state_path(context.settings.storage.artifacts_dir),
        repository=context.operational.operator_control_state,
    )
    reports_dir = Path(context.settings.storage.artifacts_dir) / "reports"
    md_path = reports_dir / f"{run}.md"
    json_path = reports_dir / f"{run}.json"
    report_markdown_path = str(md_path) if md_path.exists() else ""
    report_json_path = str(json_path) if json_path.exists() else ""
    if not report_markdown_path and state.last_run_id == run:
        report_markdown_path = state.last_report_markdown_path
    if not report_json_path and state.last_run_id == run:
        report_json_path = state.last_report_json_path
    replay_shortcut, generate_report_shortcut, eval_run_shortcut, eval_window_shortcut = report_shortcuts(
        run_id=run,
        config_path=context.config_path,
        agents_config_path=context.agents_config_path,
    )
    links = incident_links(
        linked_run_id=run,
        queue_id="",
        intent_id="",
        market_id="",
    )
    return ReportsTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=available,
        note="report_data_loaded" if available else "report_data_missing",
        run_id=summary.run_id,
        started_at=summary.started_at.isoformat() if summary.started_at else "",
        finished_at=summary.finished_at.isoformat() if summary.finished_at else "",
        total_markets=summary.total_markets,
        candidates=summary.candidates,
        executed=summary.executed,
        settled=summary.settled,
        wins=summary.wins,
        losses=summary.losses,
        skipped=summary.skipped,
        event_count=summary.event_count,
        artifact_counts=chart_from_mapping(summary.artifact_counts),
        stage_timings_ms=chart_from_mapping(summary.observability.get("stage_timings_ms")),
        failure_categories=chart_from_mapping(summary.failure_categories),
        report_markdown_path=report_markdown_path,
        report_json_path=report_json_path,
        run_overview_url=links["run_url"],
        review_queue_url=links["review_queue_url"],
        sandbox_tx_url=links["sandbox_tx_url"],
        positions_url=links["position_url"],
        settlement_url=links["settlement_url"],
        replay_shortcut=replay_shortcut,
        generate_report_shortcut=generate_report_shortcut,
        eval_run_shortcut=eval_run_shortcut,
        eval_window_shortcut=eval_window_shortcut,
        incidents_feed=queries.incident_events(run_id=run, limit=20),
    )
