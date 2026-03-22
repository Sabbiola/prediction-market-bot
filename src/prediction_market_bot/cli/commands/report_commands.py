from __future__ import annotations

import json
import logging
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.services import (
    PaperPortfolioEngine,
    build_shadow_scoring_report,
    generate_report_markdown,
    load_operator_state,
    render_shadow_scoring_report_markdown,
    replay_run,
    write_report,
)

from prediction_market_bot.cli.common import (
    build_command_profiler,
    control_state_path,
    emit_command_profile,
    summary_is_empty,
)

logger = logging.getLogger(__name__)


def generate_report_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str,
    output_path: Path | None = None,
    *,
    profile: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)
    with profiler.measure("replay_query"):
        summary = replay_run(persistence, run_id)
    if summary_is_empty(summary):
        logger.error("run_not_found", extra={"event": "run_not_found", "run_id": run_id})
        emit_command_profile(profiler=profiler, logger=logger, command="generate-report", run_id=run_id)
        return 1

    with profiler.measure("report_render_markdown"):
        report = generate_report_markdown(summary)
    destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"{run_id}.md"
    with profiler.measure("report_write_file"):
        written = write_report(destination, report)
    logger.info(
        "report_generated",
        extra={
            "event": "report_generated",
            "run_id": run_id,
            "output_path": str(written),
        },
    )
    emit_command_profile(profiler=profiler, logger=logger, command="generate-report", run_id=run_id)
    return 0


def paper_portfolio_state_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    rows = persistence.read_all_artifact_records("paper_portfolio_events")
    if run_id is not None:
        rows = [row for row in rows if row.get("run_id") == run_id]

    engine = PaperPortfolioEngine(open_positions_repo=operational.open_positions)
    restored = engine.restore_from_repository()
    if not restored:
        engine.replay_rows(rows)
    snapshot = engine.snapshot()
    output = snapshot.to_dict()
    print(json.dumps(output, indent=2))

    logger.info(
        "paper_portfolio_state",
        extra={
            "event": "paper_portfolio_state",
            "run_id": run_id or "",
            "event_count": len(rows),
            "position_count": snapshot.position_count,
            "total_exposure_usd": snapshot.total_exposure_usd,
            "realized_pnl_usd": snapshot.realized_pnl_usd,
            "unrealized_pnl_usd": snapshot.unrealized_pnl_usd,
        },
    )
    return 0


def generate_shadow_report_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str,
    output_path: Path | None = None,
    *,
    as_json: bool = False,
    profile: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)
    with profiler.measure("shadow_report_query"):
        report = build_shadow_scoring_report(persistence, run_id)

    if report.total_rows == 0:
        logger.error(
            "shadow_report_not_found",
            extra={
                "event": "shadow_report_not_found",
                "run_id": run_id,
                "warnings": list(report.warnings),
            },
        )
        emit_command_profile(profiler=profiler, logger=logger, command="generate-shadow-report", run_id=run_id)
        return 1

    if as_json:
        with profiler.measure("shadow_report_render_json"):
            print(json.dumps(report.to_dict(), indent=2))
        logger.info(
            "shadow_report_generated",
            extra={
                "event": "shadow_report_generated",
                "run_id": run_id,
                "format": "json",
                "total_rows": report.total_rows,
                "rows_with_model_v2": report.rows_with_model_v2,
                "rows_with_alt_llm_shadow": report.rows_with_alt_llm_shadow,
                "rows_with_parity_warnings": report.rows_with_parity_warnings,
            },
        )
        emit_command_profile(profiler=profiler, logger=logger, command="generate-shadow-report", run_id=run_id)
        return 0

    with profiler.measure("shadow_report_render_markdown"):
        markdown = render_shadow_scoring_report_markdown(report)
    destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"{run_id}.shadow.md"
    with profiler.measure("shadow_report_write_file"):
        written = write_report(destination, markdown)
    logger.info(
        "shadow_report_generated",
        extra={
            "event": "shadow_report_generated",
            "run_id": run_id,
            "format": "markdown",
            "output_path": str(written),
            "total_rows": report.total_rows,
            "rows_with_model_v2": report.rows_with_model_v2,
            "rows_with_alt_llm_shadow": report.rows_with_alt_llm_shadow,
            "rows_with_parity_warnings": report.rows_with_parity_warnings,
        },
    )
    emit_command_profile(profiler=profiler, logger=logger, command="generate-shadow-report", run_id=run_id)
    return 0


def last_report_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    report_format: str,
    path_only: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    operational = build_operational_repositories(settings)
    state = load_operator_state(control_state_path(settings), repository=operational.operator_control_state)
    reports_dir = Path(settings.storage.artifacts_dir) / "reports"

    if report_format == "json":
        candidate = Path(state.last_report_json_path) if state.last_report_json_path else reports_dir / "latest.json"
    else:
        candidate = (
            Path(state.last_report_markdown_path)
            if state.last_report_markdown_path
            else reports_dir / "latest.md"
        )
    if not candidate.exists():
        print(f"No {report_format} report available.")
        return 1

    if path_only:
        print(str(candidate))
        return 0

    print(f"Last {report_format} report: {candidate}")
    print(candidate.read_text(encoding="utf-8"))
    return 0
