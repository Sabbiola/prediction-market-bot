from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Mapping, Sequence

from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.bootstrap import (
    build_coordinator,
    build_http_client,
    build_live_market_data_provider,
    build_operational_repositories,
    build_live_research_pipeline,
    build_persistence,
)
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.domain.enums import (
    ExecutionStatus,
    ResolutionStatus,
    SettlementRequestState,
    TradeReviewAction,
    TradeReviewStatus,
)
from prediction_market_bot.domain.models import (
    ExecutionResult,
    MarketSnapshot,
    PendingSettlementRequest,
    PredictionResult,
    ResearchPacket,
    TradeReviewItem,
)
from prediction_market_bot.infrastructure import JsonlPersistence, SandboxChainExecutor, SqliteOperationalRepositories
from prediction_market_bot.services import (
    DeterministicResolutionPoller,
    OperatorControlState,
    PaperPortfolioEngine,
    RuntimeMetricsSnapshot,
    SandboxTransactionService,
    SettlementRequestQueueService,
    StartupValidationReport,
    collect_runtime_metrics,
    evaluate_window,
    generate_eval_report_markdown,
    generate_report_markdown,
    load_operator_state,
    list_run_ids,
    operator_state_path,
    replay_run,
    save_operator_state,
    TradeReviewQueueService,
    TxStatusSnapshot,
    validate_startup,
    write_prometheus_textfile,
    write_report,
)

logger = logging.getLogger(__name__)


def _control_state_path(settings: AppSettings) -> Path:
    return operator_state_path(settings.storage.artifacts_dir)


def _write_json_file(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _resolve_run_id(persistence: JsonlPersistence, state: OperatorControlState) -> str | None:
    if state.last_run_id.strip():
        return state.last_run_id.strip()
    run_ids = list_run_ids(persistence, limit_runs=1)
    if run_ids:
        return run_ids[-1]
    return None


def _run_startup_validation(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: SqliteOperationalRepositories,
) -> StartupValidationReport:
    return validate_startup(
        settings=settings,
        persistence=persistence,
        operational=operational,
    )


def _print_startup_report(report: StartupValidationReport) -> None:
    for check in report.checks:
        state = "OK" if check.ok else "FAIL"
        print(f"[{state}] {check.name}: {check.detail}")


def _maybe_write_runtime_metrics(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: SqliteOperationalRepositories,
) -> RuntimeMetricsSnapshot | None:
    if not settings.metrics.enabled:
        return None
    if settings.metrics.exporter.strip().lower() != "prometheus_textfile":
        return None
    snapshot = collect_runtime_metrics(
        settings=settings,
        persistence=persistence,
        operational=operational,
    )
    write_prometheus_textfile(settings.metrics.path, snapshot)
    return snapshot


def run_once_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str | None = None,
    *,
    force: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    state_path = _control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)

    if state.paused and not force:
        message = f"Run blocked: operator pause is active. reason='{state.pause_reason or 'not_set'}'"
        print(message)
        logger.warning(
            "run_once_blocked_by_pause",
            extra={
                "event": "run_once_blocked_by_pause",
                "pause_reason": state.pause_reason,
            },
        )
        return 2

    effective_run_id = run_id or f"manual-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    state.last_run_id = effective_run_id
    state.last_run_status = "running"
    state.last_run_started_at = datetime.now(UTC).isoformat()
    state.last_error = ""
    save_operator_state(state_path, state, repository=operational.operator_control_state)

    coordinator = build_coordinator(settings, persistence)
    try:
        summary = coordinator.run_dry(run_id=effective_run_id)
    except Exception as exc:
        state.last_run_status = "failed"
        state.last_run_finished_at = datetime.now(UTC).isoformat()
        state.last_error = str(exc)
        save_operator_state(state_path, state, repository=operational.operator_control_state)
        logger.exception(
            "run_once_failed",
            extra={
                "event": "run_once_failed",
                "run_id": effective_run_id,
            },
        )
        print(f"Run failed: {effective_run_id}. error={exc}")
        return 1

    replay_summary = replay_run(persistence, effective_run_id)
    reports_dir = Path(settings.storage.artifacts_dir) / "reports"
    markdown_report = generate_report_markdown(replay_summary)
    json_report_payload = replay_summary.to_dict(include_records=True)
    md_path = write_report(reports_dir / f"{effective_run_id}.md", markdown_report)
    json_path = _write_json_file(reports_dir / f"{effective_run_id}.json", json_report_payload)
    write_report(reports_dir / "latest.md", markdown_report)
    _write_json_file(reports_dir / "latest.json", json_report_payload)

    state.last_run_status = "success"
    state.last_run_finished_at = datetime.now(UTC).isoformat()
    state.last_error = ""
    state.last_report_markdown_path = str(md_path)
    state.last_report_json_path = str(json_path)
    save_operator_state(state_path, state, repository=operational.operator_control_state)

    logger.info(
        "dry_run_summary",
        extra={
            "event": "dry_run_summary",
            "run_id": summary.run_id,
            "total_markets": summary.total_markets,
            "candidates": summary.candidates,
            "executed": summary.executed_count,
            "settled": summary.settled_count,
            "wins": summary.wins,
            "losses": summary.losses,
            "skipped": summary.skipped,
            "report_markdown_path": str(md_path),
            "report_json_path": str(json_path),
        },
    )
    print(
        "Run completed "
        f"run_id={summary.run_id} executed={summary.executed_count} settled={summary.settled_count} "
        f"wins={summary.wins} losses={summary.losses} skipped={summary.skipped}"
    )
    print(f"Reports written: markdown={md_path} json={json_path}")
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def settle_run_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    state = load_operator_state(_control_state_path(settings), repository=operational.operator_control_state)
    resolved_run_id = run_id or _resolve_run_id(persistence, state)
    if not resolved_run_id:
        print("No run available to settle.")
        return 1

    settlement_queue = SettlementRequestQueueService(persistence, pending_repo=operational.pending_settlements)
    pending_requests = settlement_queue.list_requests(
        run_id=resolved_run_id,
        state=SettlementRequestState.PENDING,
        limit=0,
    )
    if not pending_requests:
        print(f"No pending settlement requests for run_id={resolved_run_id}.")
        _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
        return 0

    execution_payloads = _latest_payload_index(persistence.read_artifact_records(resolved_run_id, "execution_results"))
    prediction_payloads = _latest_payload_index(persistence.read_artifact_records(resolved_run_id, "prediction_results"))
    research_payloads = _latest_payload_index(persistence.read_artifact_records(resolved_run_id, "research_packets"))

    portfolio = PaperPortfolioEngine(
        persistence=persistence,
        open_positions_repo=operational.open_positions,
    )
    restored = portfolio.restore_from_repository()
    if not restored:
        portfolio.replay_rows(persistence.read_all_artifact_records("paper_portfolio_events"))
    settlement_agent = SettlementAgent()
    postmortem_agent = PostmortemAgent()
    resolution_poller = DeterministicResolutionPoller()

    settled_now = 0
    postmortems_now = 0
    unresolved_now = 0
    settled_markets: set[str] = set()
    for request in pending_requests:
        resolution = resolution_poller.poll(request)
        persistence.write_artifact(
            resolved_run_id,
            "resolution_checks",
            {
                "request_id": request.request_id,
                "market_id": request.market_id,
                "status": resolution.status.value,
                "resolved_yes": resolution.resolved_yes,
                "reason": resolution.reason,
                "checked_at": resolution.checked_at.isoformat(),
            },
        )
        if resolution.status != ResolutionStatus.RESOLVED or resolution.resolved_yes is None:
            unresolved_now += 1
            continue

        execution_payload = execution_payloads.get(request.market_id)
        if isinstance(execution_payload, Mapping):
            try:
                execution = ExecutionResult.model_validate(execution_payload, strict=False)
            except Exception:
                execution = _execution_from_settlement_request(request)
        else:
            execution = _execution_from_settlement_request(request)

        settlement = settlement_agent.settle(execution, resolved_yes=resolution.resolved_yes)
        persistence.write_artifact(resolved_run_id, "settlement_results", settlement.model_dump(mode="json"))
        settlement_queue.mark_settled(request=request, resolution=resolution)
        settled_now += 1

        if execution.status == ExecutionStatus.FILLED and execution.market_id not in settled_markets:
            portfolio.settle_market(
                run_id=resolved_run_id,
                market_id=execution.market_id,
                resolved_yes=resolution.resolved_yes,
            )
            settled_markets.add(execution.market_id)

        prediction_payload = prediction_payloads.get(execution.market_id)
        research_payload = research_payloads.get(execution.market_id)
        if not isinstance(prediction_payload, Mapping) or not isinstance(research_payload, Mapping):
            continue
        try:
            prediction = PredictionResult.model_validate(prediction_payload, strict=False)
            research = ResearchPacket.model_validate(research_payload, strict=False)
        except Exception:
            continue
        postmortem = postmortem_agent.run(settlement, prediction, research)
        persistence.write_artifact(resolved_run_id, "postmortems", postmortem.model_dump(mode="json"))
        postmortems_now += 1

    persistence.write_run_event(
        resolved_run_id,
        "settlement_lane_completed",
        {
            "run_id": resolved_run_id,
            "settled_now": settled_now,
            "unresolved_now": unresolved_now,
            "postmortems_now": postmortems_now,
        },
    )
    logger.info(
        "settlement_lane_completed",
        extra={
            "event": "settlement_lane_completed",
            "run_id": resolved_run_id,
            "settled_now": settled_now,
            "unresolved_now": unresolved_now,
            "postmortems_now": postmortems_now,
        },
    )
    print(
        f"Settlement lane completed run_id={resolved_run_id} "
        f"settled_now={settled_now} unresolved_now={unresolved_now} postmortems_now={postmortems_now}"
    )
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def replay_run_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str | None,
    *,
    include_records: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(_control_state_path(settings), repository=operational.operator_control_state)
    resolved_run_id = run_id or _resolve_run_id(persistence, state)
    if not resolved_run_id:
        print("No run available to replay.")
        return 1

    summary = replay_run(persistence, resolved_run_id)
    if _summary_is_empty(summary):
        logger.error("run_not_found", extra={"event": "run_not_found", "run_id": resolved_run_id})
        return 1

    print(json.dumps(summary.to_dict(include_records=include_records), indent=2))

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
    return 0


def generate_report_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str,
    output_path: Path | None = None,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    summary = replay_run(persistence, run_id)
    if _summary_is_empty(summary):
        logger.error("run_not_found", extra={"event": "run_not_found", "run_id": run_id})
        return 1

    report = generate_report_markdown(summary)
    destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"{run_id}.md"
    written = write_report(destination, report)
    logger.info(
        "report_generated",
        extra={
            "event": "report_generated",
            "run_id": run_id,
            "output_path": str(written),
        },
    )
    return 0


def evaluate_window_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    date_from: date | None,
    date_to: date | None,
    limit_runs: int,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    window = evaluate_window(
        persistence,
        date_from=date_from,
        date_to=date_to,
        limit_runs=limit_runs,
    )
    print(json.dumps(window.to_dict(), indent=2))
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
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)

    if run_id:
        summary = replay_run(persistence, run_id)
        if _summary_is_empty(summary):
            logger.error("run_not_found", extra={"event": "run_not_found", "run_id": run_id})
            return 1
        report = generate_eval_report_markdown(summary)
        destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"eval-{run_id}.md"
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
        return 0

    window = evaluate_window(
        persistence,
        date_from=date_from,
        date_to=date_to,
        limit_runs=limit_runs,
    )
    report = generate_eval_report_markdown(window)
    window_label = f"{date_from.isoformat() if date_from else 'all'}-{date_to.isoformat() if date_to else 'all'}"
    destination = output_path or Path(settings.storage.artifacts_dir) / "reports" / f"eval-window-{window_label}.md"
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
    return 0


def smoke_live_data_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    endpoint_url: str,
    timeout_sec: float,
    retries: int,
    retry_backoff_sec: float,
    max_staleness_sec: int,
    limit: int,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    http_client = build_http_client(
        settings,
        timeout_sec=timeout_sec,
        max_retries=retries,
        retry_backoff_sec=retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        cache_ttl_sec=0,
    )
    adapter = build_live_market_data_provider(
        settings,
        persistence,
        http_client,
        endpoint_url=endpoint_url,
        timeout_sec=timeout_sec,
        max_retries=retries,
        retry_backoff_sec=retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        max_staleness_seconds=max_staleness_sec,
        default_limit=limit,
    )

    try:
        batch = adapter.fetch_batch(run_id=run_id, limit=limit)
    except Exception as exc:
        logger.exception(
            "live_data_smoke_failed",
            extra={
                "event": "live_data_smoke_failed",
                "endpoint_url": endpoint_url,
                "timeout_sec": timeout_sec,
                "retries": retries,
                "max_staleness_sec": max_staleness_sec,
                "limit": limit,
                "error": str(exc),
            },
        )
        print(f"Smoke live data failed: {exc}")
        return 1

    logger.info(
        "live_data_smoke_summary",
        extra={
            "event": "live_data_smoke_summary",
            "run_id": batch.run_id,
            "endpoint_url": endpoint_url,
            "fetched_count": batch.fetched_count,
            "normalized_count": batch.normalized_count,
            "stale_count": batch.stale_count,
            "invalid_count": batch.invalid_count,
            "retries_used": batch.retries_used,
        },
    )
    print(
        "Smoke live data completed "
        f"run_id={batch.run_id} fetched={batch.fetched_count} normalized={batch.normalized_count} "
        f"stale={batch.stale_count} invalid={batch.invalid_count} retries_used={batch.retries_used}"
    )
    if batch.normalized_count == 0:
        print("Smoke live data failed: no normalized snapshots were produced.")
        return 1
    return 0


def smoke_live_research_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    market_id: str,
    title: str,
    category: str,
    query: str | None,
    limit_per_source: int,
    wikipedia_endpoint: str,
    openalex_endpoint: str,
    timeout_sec: float,
    retries: int,
    retry_backoff_sec: float,
    cache_ttl_sec: int,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    http_client = build_http_client(
        settings,
        timeout_sec=timeout_sec,
        max_retries=retries,
        retry_backoff_sec=retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        cache_ttl_sec=cache_ttl_sec,
    )
    pipeline = build_live_research_pipeline(
        settings,
        persistence,
        http_client,
        wikipedia_endpoint_url=wikipedia_endpoint,
        openalex_endpoint_url=openalex_endpoint,
        timeout_sec=timeout_sec,
        max_retries=retries,
        retry_backoff_sec=retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        cache_ttl_seconds=cache_ttl_sec,
        default_limit_per_source=limit_per_source,
    )
    market = MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue=settings.venue,
        title=title,
        yes_price=0.5,
        liquidity_usd=10_000.0,
        volume_24h_usd=5_000.0,
        spread_bps=100,
        hours_to_resolution=24.0,
        last_price_move_bps=0,
        category=category,
    )

    try:
        batch = pipeline.ingest(
            market,
            run_id=run_id,
            query_override=query,
            limit_per_source=limit_per_source,
        )
    except Exception as exc:
        logger.exception(
            "live_research_smoke_failed",
            extra={
                "event": "live_research_smoke_failed",
                "market_id": market_id,
                "source_count": len(pipeline.sources),
                "timeout_sec": timeout_sec,
                "retries": retries,
                "cache_ttl_sec": cache_ttl_sec,
                "limit_per_source": limit_per_source,
                "error": str(exc),
            },
        )
        print(f"Smoke live research failed: {exc}")
        return 1

    logger.info(
        "live_research_smoke_summary",
        extra={
            "event": "live_research_smoke_summary",
            "run_id": batch.run_id,
            "market_id": batch.market_id,
            "source_count": batch.source_count,
            "raw_count": batch.raw_count,
            "normalized_count": batch.normalized_count,
            "deduplicated_count": batch.deduplicated_count,
            "retries_used": batch.retries_used,
            "cache_hits": batch.cache_hits,
        },
    )
    print(
        "Smoke live research completed "
        f"run_id={batch.run_id} market_id={batch.market_id} sources={batch.source_count} "
        f"raw={batch.raw_count} normalized={batch.normalized_count} deduped={batch.deduplicated_count} "
        f"source_failures={batch.source_failures} retries_used={batch.retries_used} cache_hits={batch.cache_hits}"
    )
    if batch.source_failures >= batch.source_count:
        print("Smoke live research failed: all configured sources failed.")
        return 1
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


def _print_review_item(item: TradeReviewItem) -> None:
    expires_at = item.expires_at
    print(
        f"- queue_id={item.queue_id} run_id={item.run_id} market_id={item.market_id} "
        f"status={item.status.value} side={item.side.value} stake_usd={item.stake_usd:.2f} "
        f"confidence={item.confidence:.4f} edge={item.edge:.4f}"
    )
    if expires_at is not None:
        print(f"  expires_at={expires_at.isoformat()}")
    if item.model_rationale:
        print(f"  model_rationale={'; '.join(item.model_rationale)}")
    if item.operator_rationale:
        print(f"  operator_rationale={item.operator_rationale}")
    if item.notes:
        print(f"  notes={'; '.join(item.notes)}")


def review_list_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    status: str,
    limit: int,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    status_filter = _parse_review_status(status)

    items = queue.list_queue(
        run_id=run_id,
        status=status_filter,
        limit=limit,
    )
    payload = [item.model_dump(mode="json") for item in items]

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Review queue items: {len(items)}")
        for item in items:
            _print_review_item(item)

    logger.info(
        "review_queue_listed",
        extra={
            "event": "review_queue_listed",
            "run_id_filter": run_id or "",
            "status_filter": status_filter.value if status_filter is not None else "ALL",
            "item_count": len(items),
            "limit": limit,
        },
    )
    return 0


def review_show_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    item = queue.get_item(queue_id.strip())
    if item is None:
        print(f"Review queue item not found: {queue_id}")
        return 1

    payload = item.model_dump(mode="json")
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        _print_review_item(item)

    logger.info(
        "review_queue_item_shown",
        extra={
            "event": "review_queue_item_shown",
            "queue_id": item.queue_id,
            "status": item.status.value,
            "run_id": item.run_id,
        },
    )
    return 0


def _review_action_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    action: TradeReviewAction,
    operator_id: str,
    rationale: str,
    note: str,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )

    try:
        item = queue.apply_action(
            queue_id=queue_id,
            action=action,
            operator_id=operator_id,
            operator_rationale=rationale,
            note=note,
        )
    except KeyError:
        print(f"Review queue item not found: {queue_id}")
        return 1

    print(json.dumps(item.model_dump(mode="json"), indent=2))
    logger.info(
        "review_action_recorded",
        extra={
            "event": "review_action_recorded",
            "queue_id": queue_id,
            "action": action.value,
            "operator_id": operator_id,
            "status_after_action": item.status.value,
        },
    )
    return 0


def review_action_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    action: str,
    operator_id: str,
    rationale: str,
    note: str,
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=_parse_review_action(action),
        operator_id=operator_id,
        rationale=rationale,
        note=note,
    )


def review_approve_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    operator_id: str,
    rationale: str,
    note: str,
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=TradeReviewAction.APPROVE,
        operator_id=operator_id,
        rationale=rationale,
        note=note,
    )


def review_reject_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    operator_id: str,
    rationale: str,
    note: str,
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=TradeReviewAction.REJECT,
        operator_id=operator_id,
        rationale=rationale,
        note=note,
    )


def status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(_control_state_path(settings), repository=operational.operator_control_state)
    run_ids = list_run_ids(persistence, limit_runs=1000)
    latest_run_id = run_ids[-1] if run_ids else ""

    portfolio_rows = persistence.read_all_artifact_records("paper_portfolio_events")
    portfolio_engine = PaperPortfolioEngine(open_positions_repo=operational.open_positions)
    restored = portfolio_engine.restore_from_repository()
    if not restored:
        portfolio_engine.replay_rows(portfolio_rows)
    portfolio_snapshot = portfolio_engine.snapshot()
    review_queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    pending_review_count = len(review_queue.list_queue(status=TradeReviewStatus.PENDING_REVIEW, limit=0))
    settlement_queue = SettlementRequestQueueService(
        persistence,
        pending_repo=operational.pending_settlements,
    )
    pending_settlement_count = len(settlement_queue.list_requests(state=SettlementRequestState.PENDING, limit=0))
    runtime_metrics = _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)

    payload = {
        "runtime_mode": settings.runtime.mode.value,
        "market_data_provider": settings.runtime.market_data_provider.value,
        "research_provider": settings.runtime.research_provider.value,
        "provider_failure_policy": settings.runtime.provider_failure_policy.value,
        "execution_mode": settings.execution.mode.value,
        "rehearsal_mode": settings.execution.rehearsal_mode.value,
        "rehearsal_enabled": settings.execution.enable_rehearsal_lane,
        "review_blocking_gate": settings.effective_blocking_trade_review(),
        "manual_review_queue_enabled": settings.effective_manual_review_queue(),
        "review_auto_approve": settings.execution.review_auto_approve,
        "settlement_same_run": settings.execution.settlement_same_run,
        "live_market_data_enabled": settings.live_market_data.enabled,
        "live_research_enabled": settings.live_research.enabled,
        "paused": state.paused,
        "pause_reason": state.pause_reason,
        "last_run_id": state.last_run_id or latest_run_id,
        "last_run_status": state.last_run_status,
        "last_run_started_at": state.last_run_started_at,
        "last_run_finished_at": state.last_run_finished_at,
        "last_error": state.last_error,
        "known_run_count": len(run_ids),
        "pending_review_count": pending_review_count,
        "pending_settlement_count": pending_settlement_count,
        "runtime_metrics": runtime_metrics.to_dict() if runtime_metrics is not None else {},
        "portfolio": portfolio_snapshot.to_dict(),
        "last_report_markdown_path": state.last_report_markdown_path,
        "last_report_json_path": state.last_report_json_path,
        "scheduler_last_started_at": state.scheduler_last_started_at,
        "scheduler_last_tick_at": state.scheduler_last_tick_at,
        "scheduler_iterations": state.scheduler_iterations,
    }

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status_label = "PAUSED" if state.paused else "ACTIVE"
        print(f"System status: {status_label}")
        if state.paused and state.pause_reason:
            print(f"Pause reason: {state.pause_reason}")
        print(f"Last run: {payload['last_run_id'] or 'none'} ({state.last_run_status})")
        print(
            "Runtime: "
            f"mode={payload['runtime_mode']} "
            f"market_data={payload['market_data_provider']} "
            f"research={payload['research_provider']} "
            f"failure_policy={payload['provider_failure_policy']}"
        )
        print(
            "Execution: "
            f"mode={payload['execution_mode']} "
            f"rehearsal_mode={payload['rehearsal_mode']} "
            f"review_blocking={payload['review_blocking_gate']} "
            f"settlement_same_run={payload['settlement_same_run']}"
        )
        print(f"Known runs: {len(run_ids)}")
        print(f"Pending trade reviews: {pending_review_count}")
        print(f"Pending settlement requests: {pending_settlement_count}")
        print(
            "Portfolio: "
            f"positions={portfolio_snapshot.position_count} "
            f"exposure_usd={portfolio_snapshot.total_exposure_usd:.2f} "
            f"realized_pnl_usd={portfolio_snapshot.realized_pnl_usd:.2f} "
            f"unrealized_pnl_usd={portfolio_snapshot.unrealized_pnl_usd:.2f}"
        )
        if state.last_report_markdown_path:
            print(f"Last markdown report: {state.last_report_markdown_path}")
        if state.last_report_json_path:
            print(f"Last json report: {state.last_report_json_path}")

    logger.info(
        "operator_status",
        extra={
            "event": "operator_status",
            "paused": state.paused,
            "known_run_count": len(run_ids),
            "pending_review_count": pending_review_count,
            "pending_settlement_count": pending_settlement_count,
            "last_run_id": payload["last_run_id"],
            "position_count": portfolio_snapshot.position_count,
        },
    )
    return 0


def validate_startup_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    payload = report.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Startup validation: {'OK' if report.ok else 'FAILED'}")
        _print_startup_report(report)
    return 0 if report.ok else 1


def healthcheck_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    if not settings.healthcheck.enabled:
        print("Healthcheck is disabled by configuration.")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    metrics = _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    payload = {
        "status": "ok" if report.ok else "failed",
        "timestamp": datetime.now(UTC).isoformat(),
        "runtime_mode": settings.runtime.mode.value,
        "execution_mode": settings.execution.mode.value,
        "checks": [check.to_dict() for check in report.checks],
        "metrics": metrics.to_dict() if metrics is not None else {},
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Healthcheck status: {payload['status']}")
        for check in report.checks:
            state = "OK" if check.ok else "FAIL"
            print(f"- [{state}] {check.name}: {check.detail}")
    return 0 if report.ok else 1


def pause_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    reason: str,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = _control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.paused = True
    state.pause_reason = reason.strip()
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    persistence.write_run_event("operator", "operator_pause", {"reason": state.pause_reason})
    print(f"Operator pause enabled. reason='{state.pause_reason or 'not_set'}'")
    logger.warning(
        "operator_paused",
        extra={
            "event": "operator_paused",
            "reason": state.pause_reason,
        },
    )
    return 0


def resume_command(config_path: Path, agents_config_path: Path) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = _control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.paused = False
    state.pause_reason = ""
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    persistence.write_run_event("operator", "operator_resume", {})
    print("Operator pause cleared. System resumed.")
    logger.info("operator_resumed", extra={"event": "operator_resumed"})
    return 0


def run_scheduler_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    interval_sec: float,
    max_iterations: int,
    run_id_prefix: str,
    fail_fast: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    state_path = _control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.scheduler_last_started_at = datetime.now(UTC).isoformat()
    state.scheduler_iterations = 0
    save_operator_state(state_path, state, repository=operational.operator_control_state)

    print(
        "Scheduler started "
        f"interval_sec={interval_sec} max_iterations={max_iterations if max_iterations > 0 else 'infinite'}"
    )
    shutdown_requested = False
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _request_shutdown(signum: int, frame: object) -> None:
        del frame
        nonlocal shutdown_requested
        shutdown_requested = True
        print(f"Scheduler shutdown requested via signal={signum}. Draining current cycle.")

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    iterations = 0
    try:
        while (max_iterations <= 0 or iterations < max_iterations) and not shutdown_requested:
            state = load_operator_state(state_path, repository=operational.operator_control_state)
            state.scheduler_last_tick_at = datetime.now(UTC).isoformat()
            state.scheduler_iterations = iterations + 1
            save_operator_state(state_path, state, repository=operational.operator_control_state)

            if state.paused:
                print(f"Scheduler tick {iterations + 1}: paused, skipping run.")
            else:
                run_stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                run_id = f"{run_id_prefix}-{run_stamp}-{iterations + 1:03d}"
                exit_code = run_once_command(
                    config_path=config_path,
                    agents_config_path=agents_config_path,
                    run_id=run_id,
                    force=True,
                )
                print(f"Scheduler tick {iterations + 1}: run_id={run_id} exit_code={exit_code}")
                if exit_code != 0 and fail_fast:
                    print("Scheduler stopping due to fail-fast policy.")
                    return exit_code

            iterations += 1
            if max_iterations > 0 and iterations >= max_iterations:
                break
            if interval_sec > 0 and not shutdown_requested:
                sleep_remaining = interval_sec
                while sleep_remaining > 0 and not shutdown_requested:
                    step = min(sleep_remaining, 1.0)
                    time.sleep(step)
                    sleep_remaining -= step
    except KeyboardInterrupt:
        print("Scheduler interrupted by operator.")
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    print(f"Scheduler completed iterations={iterations}.")
    logger.info(
        "scheduler_completed",
        extra={
            "event": "scheduler_completed",
            "iterations": iterations,
            "shutdown_requested": shutdown_requested,
        },
    )
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
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
    state = load_operator_state(_control_state_path(settings), repository=operational.operator_control_state)
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


def _build_sandbox_tx_service(settings: AppSettings) -> SandboxTransactionService:
    operational = build_operational_repositories(settings)
    http_client = build_http_client(settings)
    private_key = ""
    private_key_env = settings.sandbox_chain.private_key_env.strip()
    if private_key_env:
        private_key = os.getenv(private_key_env, "").strip()
    executor = SandboxChainExecutor(
        rpc_url=settings.sandbox_chain.rpc_url,
        contract_address=settings.sandbox_chain.contract_address,
        chain_id=settings.sandbox_chain.chain_id,
        from_address=settings.sandbox_chain.from_address,
        intent_method_selector=settings.sandbox_chain.intent_method_selector,
        submit_tx=settings.sandbox_chain.submit_tx,
        private_key=private_key,
        allow_unlocked_send=settings.sandbox_chain.allow_unlocked_send,
        gas_limit=settings.sandbox_chain.gas_limit,
        confirmations_required=settings.sandbox_chain.confirmations_required,
        dropped_after_sec=settings.sandbox_chain.dropped_after_sec,
        timeout_sec=settings.sandbox_chain.request_timeout_sec,
        max_retries=settings.http.max_retries,
        retry_backoff_sec=settings.http.retry_backoff_sec,
        retry_jitter_sec=settings.http.retry_jitter_sec,
        enabled=settings.sandbox_chain.enabled,
        http_client=http_client,
    )
    return SandboxTransactionService(
        executor=executor,
        intent_repo=operational.transaction_intents,
        attempt_repo=operational.transaction_attempts,
        receipt_repo=operational.transaction_receipts,
    )


def _render_tx_snapshots(rows: list[TxStatusSnapshot], *, as_json: bool) -> None:
    payload = [row.to_dict() for row in rows]
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"Transaction statuses: {len(rows)}")
    for row in rows:
        print(
            f"- intent_id={row.intent_id} run_id={row.run_id} market_id={row.market_id} "
            f"status={row.confirmation_status.value} tx_hash={row.latest_tx_hash or 'n/a'} "
            f"nonce={row.nonce if row.nonce is not None else 'n/a'} retries={row.retry_count}"
        )
        if row.review_queue_id:
            print(f"  review_queue_id={row.review_queue_id}")
        if row.message:
            print(f"  message={row.message}")


def tx_status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    intent_id: str | None,
    limit: int,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    service = _build_sandbox_tx_service(settings)
    rows = service.list_status(run_id=run_id, intent_id=intent_id, limit=limit)
    _render_tx_snapshots(rows, as_json=as_json)
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def tx_reconcile_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    intent_id: str | None,
    limit: int,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    service = _build_sandbox_tx_service(settings)
    rows = service.reconcile(run_id=run_id, intent_id=intent_id, limit=limit)
    _render_tx_snapshots(rows, as_json=as_json)
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def tx_resubmit_safe_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    intent_id: str,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = _run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        _print_startup_report(startup_report)
        return 1
    if not settings.sandbox_chain.submit_tx:
        print("tx-resubmit-safe requires sandbox_chain.submit_tx=true")
        return 2
    service = _build_sandbox_tx_service(settings)
    try:
        row = service.resubmit_safe(intent_id=intent_id.strip())
    except KeyError:
        print(f"Transaction intent not found: {intent_id}")
        return 1
    except ValueError as exc:
        print(f"Safe resubmit blocked: {exc}")
        return 2
    _render_tx_snapshots([row], as_json=as_json)
    _maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prediction-market-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_once = subparsers.add_parser("run-once", help="Run one complete paper-live cycle (no real order posting).")
    run_once.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_once.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_once.add_argument("--run-id", default=None, help="Optional explicit run id for deterministic replay.")
    run_once.add_argument(
        "--force",
        action="store_true",
        help="Run even when operator pause is active (intended for controlled CI operations).",
    )

    run_alias = subparsers.add_parser("run", help="Alias of run-once.")
    run_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    run_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    run_alias.add_argument("--run-id", default=None, help="Optional explicit run id for deterministic replay.")
    run_alias.add_argument(
        "--force",
        action="store_true",
        help="Run even when operator pause is active (intended for controlled CI operations).",
    )

    settle_run = subparsers.add_parser(
        "settle-run",
        help="Run settlement lane for pending settlement requests of a run.",
    )
    settle_run.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    settle_run.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    settle_run.add_argument(
        "--run-id",
        default=None,
        help="Run id to settle. Defaults to latest known run.",
    )

    settlement_lane = subparsers.add_parser("run-settlement-lane", help="Alias of settle-run.")
    settlement_lane.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    settlement_lane.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    settlement_lane.add_argument(
        "--run-id",
        default=None,
        help="Run id to settle. Defaults to latest known run.",
    )

    status = subparsers.add_parser("status", help="Show operator-oriented system status.")
    status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    status.add_argument("--json", action="store_true", help="Print status payload as JSON.")

    healthcheck = subparsers.add_parser(
        "healthcheck",
        help="Run startup/dependency/config checks and emit staging health payload.",
    )
    healthcheck.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    healthcheck.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    healthcheck.add_argument("--json", action="store_true", help="Print health payload as JSON.")

    validate_startup = subparsers.add_parser(
        "validate-startup",
        help="Validate runtime dependencies and configuration without running a trading cycle.",
    )
    validate_startup.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    validate_startup.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    validate_startup.add_argument("--json", action="store_true", help="Print validation payload as JSON.")

    scheduler = subparsers.add_parser("run-scheduler", help="Run dry-run scheduler loop from terminal/CI.")
    scheduler.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    scheduler.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    scheduler.add_argument("--interval-sec", type=float, default=60.0, help="Seconds between scheduler ticks.")
    scheduler.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N iterations. 0 means run until interrupted.",
    )
    scheduler.add_argument("--run-id-prefix", default="sched", help="Prefix used to generate scheduler run ids.")
    scheduler.add_argument("--fail-fast", action="store_true", help="Stop scheduler if one run exits non-zero.")

    pause = subparsers.add_parser("pause", help="Pause operator execution flow.")
    pause.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    pause.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    pause.add_argument("--reason", default="manual_operator_pause", help="Operator pause reason.")

    resume = subparsers.add_parser("resume", help="Resume operator execution flow.")
    resume.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    resume.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )

    review_list = subparsers.add_parser("review-list", help="List trade review candidates.")
    review_list.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_list.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_list.add_argument("--run-id", default=None, help="Optional run id filter.")
    review_list.add_argument(
        "--status",
        choices=("pending_review", "pending", "approved", "rejected", "expired", "all"),
        default="pending_review",
        help="Filter review queue by status.",
    )
    review_list.add_argument("--limit", type=int, default=50, help="Maximum queue items to return. 0 means all.")
    review_list.add_argument("--json", action="store_true", help="Print queue items as JSON.")

    review_show = subparsers.add_parser("review-show", help="Show one trade review candidate by queue id.")
    review_show.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_show.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_show.add_argument("--queue-id", required=True, help="Review queue id.")
    review_show.add_argument("--json", action="store_true", help="Print queue item as JSON.")

    review_approve = subparsers.add_parser("review-approve", help="Approve a trade review candidate.")
    review_approve.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_approve.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_approve.add_argument("--queue-id", required=True, help="Review queue id to approve.")
    review_approve.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_approve.add_argument("--rationale", required=True, help="Operator rationale for approval.")
    review_approve.add_argument("--note", default="", help="Optional additional note.")

    review_reject = subparsers.add_parser("review-reject", help="Reject a trade review candidate.")
    review_reject.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_reject.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_reject.add_argument("--queue-id", required=True, help="Review queue id to reject.")
    review_reject.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_reject.add_argument("--rationale", required=True, help="Operator rationale for rejection.")
    review_reject.add_argument("--note", default="", help="Optional additional note.")

    review_queue = subparsers.add_parser("review-queue", help="Alias of review-list.")
    review_queue.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_queue.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_queue.add_argument("--run-id", default=None, help="Optional run id filter.")
    review_queue.add_argument(
        "--status",
        choices=("pending_review", "pending", "approved", "rejected", "expired", "all"),
        default="pending_review",
        help="Filter review queue by status.",
    )
    review_queue.add_argument("--limit", type=int, default=50, help="Maximum queue items to return. 0 means all.")
    review_queue.add_argument("--json", action="store_true", help="Print queue items as JSON.")

    review_action = subparsers.add_parser("review-action", help="Apply approve/reject/note on a review queue item.")
    review_action.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    review_action.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    review_action.add_argument("--queue-id", required=True, help="Review queue id to update.")
    review_action.add_argument(
        "--action",
        choices=("approve", "reject", "note"),
        required=True,
        help="Operator action.",
    )
    review_action.add_argument("--operator-id", default="operator", help="Operator identifier.")
    review_action.add_argument("--rationale", required=True, help="Operator rationale for the action.")
    review_action.add_argument("--note", default="", help="Optional additional note.")

    replay = subparsers.add_parser("replay-run", help="Replay a stored run summary from persisted artifacts.")
    replay.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    replay.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    replay.add_argument("--run-id", required=True, help="Run id to replay.")
    replay.add_argument(
        "--include-records",
        action="store_true",
        help="Include reconstructed per-market decisions in stdout JSON output.",
    )

    replay_alias = subparsers.add_parser("replay", help="Alias of replay-run with optional --run-id fallback.")
    replay_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    replay_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    replay_alias.add_argument("--run-id", default=None, help="Run id to replay. Defaults to latest known run.")
    replay_alias.add_argument(
        "--include-records",
        action="store_true",
        help="Include reconstructed per-market decisions in stdout JSON output.",
    )

    eval_window = subparsers.add_parser("evaluate-window", help="Evaluate a run window from persisted artifacts.")
    eval_window.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    eval_window.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    eval_window.add_argument("--date-from", default=None, help="Inclusive start date (YYYY-MM-DD).")
    eval_window.add_argument("--date-to", default=None, help="Inclusive end date (YYYY-MM-DD).")
    eval_window.add_argument("--limit-runs", type=int, default=50, help="Maximum number of runs to evaluate.")

    eval_report = subparsers.add_parser(
        "generate-eval-report",
        help="Generate a markdown evaluation report for one run or a date window.",
    )
    eval_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    eval_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    eval_report.add_argument("--run-id", default=None, help="Optional run id to evaluate.")
    eval_report.add_argument("--date-from", default=None, help="Inclusive start date (YYYY-MM-DD).")
    eval_report.add_argument("--date-to", default=None, help="Inclusive end date (YYYY-MM-DD).")
    eval_report.add_argument("--limit-runs", type=int, default=50, help="Maximum runs when evaluating a window.")
    eval_report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")

    report = subparsers.add_parser("generate-report", help="Generate a markdown report for a stored run.")
    report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    report.add_argument("--run-id", required=True, help="Run id to report.")
    report.add_argument("--output", type=Path, default=None, help="Optional markdown output path.")

    smoke_live = subparsers.add_parser("smoke-live-data", help="Fetch one live read-only market data batch.")
    smoke_live.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    smoke_live.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    smoke_live.add_argument("--run-id", default=None, help="Optional explicit run id for this smoke fetch.")
    smoke_live.add_argument(
        "--endpoint",
        default="https://gamma-api.polymarket.com/markets",
        help="Read-only markets API endpoint.",
    )
    smoke_live.add_argument("--timeout-sec", type=float, default=8.0, help="HTTP timeout in seconds.")
    smoke_live.add_argument("--retries", type=int, default=2, help="Number of retry attempts.")
    smoke_live.add_argument(
        "--retry-backoff-sec",
        type=float,
        default=0.5,
        help="Linear backoff base used between retries.",
    )
    smoke_live.add_argument(
        "--max-staleness-sec",
        type=int,
        default=900,
        help="Maximum age in seconds for market snapshots.",
    )
    smoke_live.add_argument("--limit", type=int, default=50, help="Maximum markets requested per batch.")

    smoke_research = subparsers.add_parser(
        "smoke-live-research",
        help="Fetch one structured live research batch and persist raw/normalized findings.",
    )
    smoke_research.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    smoke_research.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    smoke_research.add_argument("--run-id", default=None, help="Optional explicit run id for this smoke fetch.")
    smoke_research.add_argument("--market-id", default="smoke-research-market", help="Synthetic market id.")
    smoke_research.add_argument(
        "--title",
        default="Will inflation decline in the next quarter?",
        help="Synthetic market title used to build the research query.",
    )
    smoke_research.add_argument("--category", default="macro", help="Synthetic market category.")
    smoke_research.add_argument("--query", default=None, help="Optional explicit query override.")
    smoke_research.add_argument("--limit-per-source", type=int, default=5, help="Max records fetched per source.")
    smoke_research.add_argument(
        "--wikipedia-endpoint",
        default="https://en.wikipedia.org/w/api.php",
        help="Wikipedia API endpoint.",
    )
    smoke_research.add_argument(
        "--openalex-endpoint",
        default="https://api.openalex.org/works",
        help="OpenAlex works API endpoint.",
    )
    smoke_research.add_argument("--timeout-sec", type=float, default=8.0, help="HTTP timeout in seconds.")
    smoke_research.add_argument("--retries", type=int, default=2, help="Number of retry attempts.")
    smoke_research.add_argument(
        "--retry-backoff-sec",
        type=float,
        default=0.5,
        help="Linear backoff base used between retries.",
    )
    smoke_research.add_argument("--cache-ttl-sec", type=int, default=600, help="In-memory HTTP cache TTL.")

    portfolio_state = subparsers.add_parser(
        "paper-portfolio-state",
        help="Print current paper portfolio state reconstructed from persisted portfolio events.",
    )
    portfolio_state.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    portfolio_state.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    portfolio_state.add_argument(
        "--run-id",
        default=None,
        help="Optional run id filter. If omitted, all persisted portfolio events are replayed.",
    )

    portfolio_alias = subparsers.add_parser("portfolio", help="Alias of paper-portfolio-state.")
    portfolio_alias.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    portfolio_alias.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    portfolio_alias.add_argument(
        "--run-id",
        default=None,
        help="Optional run id filter. If omitted, all persisted portfolio events are replayed.",
    )

    last_report = subparsers.add_parser("last-report", help="Print the latest markdown or json run report.")
    last_report.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    last_report.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    last_report.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Report format to print.",
    )
    last_report.add_argument("--path-only", action="store_true", help="Print only the resolved report path.")

    tx_status = subparsers.add_parser("tx-status", help="Show sandbox transaction status snapshots.")
    tx_status.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_status.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_status.add_argument("--run-id", default=None, help="Optional run id filter.")
    tx_status.add_argument("--intent-id", default=None, help="Optional intent id filter.")
    tx_status.add_argument("--limit", type=int, default=50, help="Maximum intents to inspect. 0 means all.")
    tx_status.add_argument("--json", action="store_true", help="Print status payload as JSON.")

    tx_reconcile = subparsers.add_parser(
        "tx-reconcile",
        help="Reconcile pending sandbox transactions against chain state.",
    )
    tx_reconcile.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_reconcile.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_reconcile.add_argument("--run-id", default=None, help="Optional run id filter.")
    tx_reconcile.add_argument("--intent-id", default=None, help="Optional intent id filter.")
    tx_reconcile.add_argument("--limit", type=int, default=100, help="Maximum intents to reconcile. 0 means all.")
    tx_reconcile.add_argument("--json", action="store_true", help="Print reconciled payload as JSON.")

    tx_resubmit_safe = subparsers.add_parser(
        "tx-resubmit-safe",
        help="Safely resubmit a sandbox transaction intent when prior attempt is not pending/mined.",
    )
    tx_resubmit_safe.add_argument("--config", default="config/app.yaml", type=Path, help="Path to app config YAML.")
    tx_resubmit_safe.add_argument(
        "--agents-config",
        default="config/agents.yaml",
        type=Path,
        help="Path to agent catalog config YAML.",
    )
    tx_resubmit_safe.add_argument("--intent-id", required=True, help="Intent id to resubmit safely.")
    tx_resubmit_safe.add_argument("--json", action="store_true", help="Print resulting tx snapshot as JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command in {"run-once", "run"}:
        return run_once_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            force=args.force,
        )
    if args.command in {"settle-run", "run-settlement-lane"}:
        return settle_run_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
        )
    if args.command == "status":
        return status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "healthcheck":
        return healthcheck_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "validate-startup":
        return validate_startup_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            as_json=args.json,
        )
    if args.command == "run-scheduler":
        return run_scheduler_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            interval_sec=args.interval_sec,
            max_iterations=args.max_iterations,
            run_id_prefix=args.run_id_prefix,
            fail_fast=args.fail_fast,
        )
    if args.command == "pause":
        return pause_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            reason=args.reason,
        )
    if args.command == "resume":
        return resume_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
        )
    if args.command in {"review-list", "review-queue"}:
        return review_list_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            status=args.status,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "review-show":
        return review_show_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            as_json=args.json,
        )
    if args.command == "review-approve":
        return review_approve_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command == "review-reject":
        return review_reject_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command == "review-action":
        return review_action_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            queue_id=args.queue_id,
            action=args.action,
            operator_id=args.operator_id,
            rationale=args.rationale,
            note=args.note,
        )
    if args.command in {"replay-run", "replay"}:
        return replay_run_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            include_records=args.include_records,
        )
    if args.command == "evaluate-window":
        return evaluate_window_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            date_from=_parse_date_arg(args.date_from),
            date_to=_parse_date_arg(args.date_to),
            limit_runs=args.limit_runs,
        )
    if args.command == "generate-eval-report":
        date_from = _parse_date_arg(args.date_from)
        date_to = _parse_date_arg(args.date_to)
        if args.run_id is None and date_from is None and date_to is None:
            parser.error("generate-eval-report requires --run-id or at least one of --date-from/--date-to.")
        return generate_eval_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            date_from=date_from,
            date_to=date_to,
            limit_runs=args.limit_runs,
            output_path=args.output,
        )
    if args.command == "generate-report":
        return generate_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            output_path=args.output,
        )
    if args.command == "smoke-live-data":
        return smoke_live_data_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            endpoint_url=args.endpoint,
            timeout_sec=args.timeout_sec,
            retries=args.retries,
            retry_backoff_sec=args.retry_backoff_sec,
            max_staleness_sec=args.max_staleness_sec,
            limit=args.limit,
        )
    if args.command == "smoke-live-research":
        return smoke_live_research_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            market_id=args.market_id,
            title=args.title,
            category=args.category,
            query=args.query,
            limit_per_source=args.limit_per_source,
            wikipedia_endpoint=args.wikipedia_endpoint,
            openalex_endpoint=args.openalex_endpoint,
            timeout_sec=args.timeout_sec,
            retries=args.retries,
            retry_backoff_sec=args.retry_backoff_sec,
            cache_ttl_sec=args.cache_ttl_sec,
        )
    if args.command in {"paper-portfolio-state", "portfolio"}:
        return paper_portfolio_state_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
        )
    if args.command == "last-report":
        return last_report_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            report_format=args.format,
            path_only=args.path_only,
        )
    if args.command == "tx-status":
        return tx_status_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            intent_id=args.intent_id,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "tx-reconcile":
        return tx_reconcile_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            run_id=args.run_id,
            intent_id=args.intent_id,
            limit=args.limit,
            as_json=args.json,
        )
    if args.command == "tx-resubmit-safe":
        return tx_resubmit_safe_command(
            config_path=args.config,
            agents_config_path=args.agents_config,
            intent_id=args.intent_id,
            as_json=args.json,
        )

    parser.error("Unknown command.")
    return 2


def _market_ids_from_rows(rows: Sequence[Mapping[str, object]]) -> set[str]:
    market_ids: set[str] = set()
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        market_id = _extract_market_id(payload)
        if market_id:
            market_ids.add(market_id)
    return market_ids


def _execution_from_settlement_request(request: PendingSettlementRequest) -> ExecutionResult:
    return ExecutionResult(
        market_id=request.market_id,
        execution_mode=request.execution_mode,
        status=request.execution_status,
        side=request.side,
        stake_usd=request.stake_usd,
        fill_price=request.fill_price,
        order_id=request.order_id,
        message="execution_reconstructed_from_pending_settlement_request",
    )


def _latest_payload_index(rows: Sequence[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    index: dict[str, Mapping[str, object]] = {}
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        market_id = _extract_market_id(payload)
        if not market_id:
            continue
        index[market_id] = payload
    return index


def _extract_market_id(payload: Mapping[str, object], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    direct = payload.get("market_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for value in payload.values():
        if isinstance(value, Mapping):
            nested = _extract_market_id(value, depth=depth + 1)
            if nested:
                return nested
    return ""


def _summary_is_empty(summary: object) -> bool:
    if not hasattr(summary, "event_count"):
        return True
    event_count = getattr(summary, "event_count")
    total_markets = getattr(summary, "total_markets", 0)
    candidates = getattr(summary, "candidates", 0)
    return int(event_count) == 0 and int(total_markets) == 0 and int(candidates) == 0


def _parse_date_arg(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_review_status(value: str) -> TradeReviewStatus | None:
    normalized = value.strip().upper()
    if normalized == "ALL":
        return None
    aliases = {
        "PENDING": "PENDING_REVIEW",
    }
    return TradeReviewStatus(aliases.get(normalized, normalized))


def _parse_review_action(value: str) -> TradeReviewAction:
    return TradeReviewAction(value.strip().upper())


if __name__ == "__main__":
    raise SystemExit(main())
