from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.services import (
    evaluate_window,
    generate_eval_report_markdown,
    replay_run,
    write_report,
)

from prediction_market_bot.cli.common import summary_is_empty
from prediction_market_bot.cli.common import build_command_profiler, emit_command_profile

logger = logging.getLogger(__name__)


def replay_run_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str | None,
    *,
    include_records: bool = False,
    profile: bool = False,
) -> int:
    from prediction_market_bot.app.bootstrap import build_operational_repositories
    from prediction_market_bot.services import load_operator_state

    from prediction_market_bot.cli.common import control_state_path, resolve_run_id

    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(control_state_path(settings), repository=operational.operator_control_state)
    resolved_run_id = run_id or resolve_run_id(persistence, state)
    if not resolved_run_id:
        print("No run available to replay.")
        return 1

    with profiler.measure("replay_query"):
        summary = replay_run(persistence, resolved_run_id)
    if summary_is_empty(summary):
        logger.error("run_not_found", extra={"event": "run_not_found", "run_id": resolved_run_id})
        emit_command_profile(profiler=profiler, logger=logger, command="replay-run", run_id=resolved_run_id)
        return 1

    with profiler.measure("serialize_output"):
        payload = summary.to_dict(include_records=include_records)
    print(json.dumps(payload, indent=2))

    logger.info(
        "replay_summary",
        extra={
            "event": "replay_summary",
            "run_id": summary.run_id,
            "total_markets": summary.total_markets,
            "candidates": summary.candidates,
            "executed": summary.executed,
            "settled": summary.settled,
            "wins": summary.wins,
            "losses": summary.losses,
            "skipped": summary.skipped,
            "event_count": summary.event_count,
            "reconstructed_count": len(summary.reconstructed_records),
            "calibration_gap": summary.calibration_metrics.calibration_gap,
            "fair_yes_brier_score": summary.brier_metrics.fair_yes_brier_score,
        },
    )
    print(f"Replay completed for run_id={summary.run_id}")
    emit_command_profile(profiler=profiler, logger=logger, command="replay-run", run_id=summary.run_id)
    return 0


def evaluate_window_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    date_from: date | None,
    date_to: date | None,
    limit_runs: int,
    profile: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)
    with profiler.measure("evaluate_window_query"):
        window = evaluate_window(
            persistence,
            date_from=date_from,
            date_to=date_to,
            limit_runs=limit_runs,
        )
    with profiler.measure("serialize_output"):
        payload = window.to_dict()
    print(json.dumps(payload, indent=2))
    logger.info(
        "evaluate_window_summary",
        extra={
            "event": "evaluate_window_summary",
            "run_count": window.run_count,
            "date_from": window.date_from.isoformat() if window.date_from else "",
            "date_to": window.date_to.isoformat() if window.date_to else "",
            "sample_size": window.calibration_metrics.sample_size,
            "calibration_gap": window.calibration_metrics.calibration_gap,
            "fair_yes_brier_score": window.brier_metrics.fair_yes_brier_score,
        },
    )
    emit_command_profile(profiler=profiler, logger=logger, command="evaluate-window")
    return 0


def generate_eval_report_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    date_from: date | None,
    date_to: date | None,
    limit_runs: int,
    output_path: Path | None = None,
    profile: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)

    if run_id:
        with profiler.measure("replay_query"):
            summary = replay_run(persistence, run_id)
        if summary_is_empty(summary):
            logger.error("run_not_found", extra={"event": "run_not_found", "run_id": run_id})
            emit_command_profile(profiler=profiler, logger=logger, command="generate-eval-report", run_id=run_id)
            return 1
        with profiler.measure("report_render_markdown"):
            report = generate_eval_report_markdown(summary)
        destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"eval-{run_id}.md"
        with profiler.measure("report_write_file"):
            written = write_report(destination, report)
        logger.info(
            "eval_report_generated",
            extra={
                "event": "eval_report_generated",
                "scope": "run",
                "run_id": run_id,
                "output_path": str(written),
            },
        )
        emit_command_profile(profiler=profiler, logger=logger, command="generate-eval-report", run_id=run_id)
        return 0

    with profiler.measure("evaluate_window_query"):
        window = evaluate_window(
            persistence,
            date_from=date_from,
            date_to=date_to,
            limit_runs=limit_runs,
        )
    with profiler.measure("report_render_markdown"):
        report = generate_eval_report_markdown(window)
    window_label = f"{date_from.isoformat() if date_from else 'all'}-{date_to.isoformat() if date_to else 'all'}"
    destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"eval-window-{window_label}.md"
    with profiler.measure("report_write_file"):
        written = write_report(destination, report)
    logger.info(
        "eval_report_generated",
        extra={
            "event": "eval_report_generated",
            "scope": "window",
            "run_count": window.run_count,
            "output_path": str(written),
        },
    )
    emit_command_profile(profiler=profiler, logger=logger, command="generate-eval-report")
    return 0
