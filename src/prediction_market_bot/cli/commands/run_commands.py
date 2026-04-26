from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.bootstrap import (
    build_coordinator,
    build_http_client,
    build_live_market_data_provider,
    build_live_research_pipeline,
    build_operational_repositories,
    build_persistence,
)
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.domain.enums import ExecutionStatus, ResolutionStatus, SettlementRequestState
from prediction_market_bot.domain.models import ExecutionResult, MarketSnapshot, PredictionResult, ResearchPacket
from prediction_market_bot.services import (
    DeterministicResolutionPoller,
    PaperPortfolioEngine,
    PolymarketResolutionPoller,
    SettlementRequestQueueService,
    generate_report_markdown,
    load_operator_state,
    replay_run,
    save_operator_state,
    write_report,
)

from prediction_market_bot.cli.common import (
    build_command_profiler,
    control_state_path,
    emit_command_profile,
    execution_from_settlement_request,
    latest_payload_index,
    maybe_write_runtime_metrics,
    print_startup_report,
    run_startup_validation,
    write_json_file,
)

logger = logging.getLogger(__name__)


def run_once_command(
    config_path: Path,
    agents_config_path: Path,
    run_id: str | None = None,
    *,
    force: bool = False,
    profile: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    profiler = build_command_profiler(settings=settings, profile=profile)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    with profiler.measure("startup_validation"):
        startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        emit_command_profile(profiler=profiler, logger=logger, command="run-once")
        return 1
    state_path = control_state_path(settings)
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
        with profiler.measure("pipeline_run"):
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
        emit_command_profile(profiler=profiler, logger=logger, command="run-once", run_id=effective_run_id)
        return 1

    with profiler.measure("replay_query"):
        replay_summary = replay_run(persistence, effective_run_id)
    reports_dir = Path(settings.storage.artifacts_dir) / "reports"
    with profiler.measure("report_render_markdown"):
        markdown_report = generate_report_markdown(replay_summary)
    with profiler.measure("report_write_files"):
        md_path = write_report(reports_dir / f"{effective_run_id}.md", markdown_report)
    json_report_payload = replay_summary.to_dict(include_records=True)
    with profiler.measure("report_write_files"):
        json_path = write_json_file(reports_dir / f"{effective_run_id}.json", json_report_payload)
        write_report(reports_dir / "latest.md", markdown_report)
        write_json_file(reports_dir / "latest.json", json_report_payload)

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
    with profiler.measure("runtime_metrics_export"):
        maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    emit_command_profile(profiler=profiler, logger=logger, command="run-once", run_id=summary.run_id)
    return 0


def settle_run_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
) -> int:
    from prediction_market_bot.cli.common import resolve_run_id

    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        return 1
    state = load_operator_state(control_state_path(settings), repository=operational.operator_control_state)
    resolved_run_id = run_id or resolve_run_id(persistence, state)
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
        maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
        return 0

    execution_payloads = latest_payload_index(persistence.read_artifact_records(resolved_run_id, "execution_results"))
    prediction_payloads = latest_payload_index(persistence.read_artifact_records(resolved_run_id, "prediction_results"))
    research_payloads = latest_payload_index(persistence.read_artifact_records(resolved_run_id, "research_packets"))

    portfolio = PaperPortfolioEngine(
        persistence=persistence,
        open_positions_repo=operational.open_positions,
    )
    restored = portfolio.restore_from_repository()
    if not restored:
        portfolio.replay_rows(persistence.read_all_artifact_records("paper_portfolio_events"))
    settlement_agent = SettlementAgent()
    postmortem_agent = PostmortemAgent()
    # Use the real Polymarket resolution poller in live/sandbox modes so that
    # settlement PnL reflects actual market outcomes (not a hash-based mock).
    from prediction_market_bot.domain.enums import RuntimeMode
    if settings.runtime.mode in {RuntimeMode.PAPER_LIVE, RuntimeMode.SANDBOX_CHAIN}:
        resolution_poller = PolymarketResolutionPoller()
    else:
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
                execution = execution_from_settlement_request(request)
        else:
            execution = execution_from_settlement_request(request)

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
    maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    return 0


def _run_cross_run_settlement_pass(
    *,
    config_path: Path,
    agents_config_path: Path,
) -> None:
    """Poll real Polymarket outcomes for every run_id with pending settlements.

    Called from the scheduler loop when ``settlement_same_run=false`` so that
    positions opened across prior ticks actually resolve against the live
    Polymarket/UMA oracle instead of piling up forever.

    Uses the same machinery as ``settle_run_command`` but applied per-run_id
    across all pending settlement requests in the queue.
    """
    settings = load_settings(config_path, agents_config_path)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = SettlementRequestQueueService(persistence, pending_repo=operational.pending_settlements)
    pending = queue.list_requests(state=SettlementRequestState.PENDING, limit=0)
    if not pending:
        return
    # Group pending requests by run_id, then delegate to the per-run command.
    run_ids = sorted({r.run_id for r in pending if r.run_id})
    print(f"Settlement pass: polling {len(pending)} pending across {len(run_ids)} run(s).")
    for run_id in run_ids:
        try:
            settle_run_command(
                config_path=config_path,
                agents_config_path=agents_config_path,
                run_id=run_id,
            )
        except Exception as exc:
            logger.warning(
                "scheduler_settlement_pass_run_failed",
                extra={
                    "event": "scheduler_settlement_pass_run_failed",
                    "run_id": run_id,
                    "error": str(exc),
                },
            )


def run_scheduler_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    interval_sec: float,
    max_iterations: int,
    run_id_prefix: str,
    fail_fast: bool,
    align_to_minutes: int = 0,
    align_offset_sec: int = 15,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    effective_interval = interval_sec if interval_sec > 0 else float(settings.runtime.scan_interval_sec)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    startup_report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    if not startup_report.ok:
        print("Startup validation failed.")
        print_startup_report(startup_report)
        return 1
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    state.scheduler_last_started_at = datetime.now(UTC).isoformat()
    state.scheduler_iterations = 0
    save_operator_state(state_path, state, repository=operational.operator_control_state)

    settlement_interval = float(getattr(settings.runtime, "settlement_interval_sec", 600))
    settlement_same_run_enabled = bool(
        getattr(settings.execution, "settlement_same_run", True)
    )
    align_mode = align_to_minutes > 0
    print(
        "Scheduler started "
        f"interval_sec={effective_interval} settlement_interval_sec={settlement_interval} "
        f"settlement_same_run={settlement_same_run_enabled} "
        f"align_to_minutes={align_to_minutes} align_offset_sec={align_offset_sec} "
        f"max_iterations={max_iterations if max_iterations > 0 else 'infinite'}"
    )
    shutdown_requested = False
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    last_settlement_pass_at = 0.0

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

                # Cross-run settlement pass — only needed when same-run fake
                # settlement is disabled. Polls real Polymarket outcomes for all
                # pending positions from prior ticks, so the loop actually
                # reflects Chainlink/UMA resolutions instead of the deterministic
                # hash fallback in the coordinator.
                now_monotonic = time.monotonic()
                if (
                    not settlement_same_run_enabled
                    and (now_monotonic - last_settlement_pass_at) >= settlement_interval
                ):
                    try:
                        _run_cross_run_settlement_pass(
                            config_path=config_path,
                            agents_config_path=agents_config_path,
                        )
                    except Exception as exc:  # pragma: no cover — scheduler robustness
                        logger.warning(
                            "scheduler_settlement_pass_failed",
                            extra={
                                "event": "scheduler_settlement_pass_failed",
                                "error": str(exc),
                            },
                        )
                        print(f"Scheduler tick {iterations + 1}: settlement pass failed: {exc}")
                    last_settlement_pass_at = now_monotonic

            iterations += 1
            if max_iterations > 0 and iterations >= max_iterations:
                break
            if shutdown_requested:
                continue

            # Slot-aligned wait: sleep until the next wall-clock boundary at
            # HH:00, HH:Nm, etc. plus a small offset (so Polymarket has time
            # to lock the slot's reference price). Falls back to fixed
            # interval when align_to_minutes <= 0.
            if align_mode:
                now = datetime.now(UTC)
                step_minutes = align_to_minutes
                # next boundary >= now
                minutes_into_step = now.minute % step_minutes
                seconds_into_step = (
                    minutes_into_step * 60 + now.second + now.microsecond / 1_000_000
                )
                step_total_sec = step_minutes * 60
                seconds_until_next_boundary = step_total_sec - seconds_into_step
                if seconds_until_next_boundary <= 0:
                    seconds_until_next_boundary += step_total_sec
                sleep_remaining = seconds_until_next_boundary + max(align_offset_sec, 0)
                # If the offset alone pushed us past a full step, drop one step
                # so we still fire at the *next* boundary, not the one after.
                while sleep_remaining > step_total_sec + align_offset_sec:
                    sleep_remaining -= step_total_sec
            elif effective_interval > 0:
                sleep_remaining = effective_interval
            else:
                sleep_remaining = 0.0

            # Always-on position monitor: while idling between ticks, keep
            # polling pending settlements every position_monitor_interval_sec
            # so UMA/Chainlink resolutions are picked up the moment they land
            # — instead of waiting until the next 15-min tick. This is what
            # gives us "continuous" position monitoring even though the
            # scan/predict/execute cycle only fires once per slot.
            position_monitor_interval = 60.0
            last_monitor_pass_at = time.monotonic()
            while sleep_remaining > 0 and not shutdown_requested:
                step = min(sleep_remaining, 1.0)
                time.sleep(step)
                sleep_remaining -= step

                if (
                    not settlement_same_run_enabled
                    and (time.monotonic() - last_monitor_pass_at) >= position_monitor_interval
                ):
                    try:
                        _run_cross_run_settlement_pass(
                            config_path=config_path,
                            agents_config_path=agents_config_path,
                        )
                    except Exception as exc:  # pragma: no cover — robustness
                        logger.warning(
                            "scheduler_position_monitor_failed",
                            extra={
                                "event": "scheduler_position_monitor_failed",
                                "error": str(exc),
                            },
                        )
                    last_monitor_pass_at = time.monotonic()
                    last_settlement_pass_at = last_monitor_pass_at
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
    maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
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
            "cache_hit": batch.cache_hit,
            "fetch_duration_ms": batch.fetch_duration_ms,
            "total_duration_ms": batch.total_duration_ms,
        },
    )
    print(
        "Smoke live data completed "
        f"run_id={batch.run_id} fetched={batch.fetched_count} normalized={batch.normalized_count} "
        f"stale={batch.stale_count} invalid={batch.invalid_count} retries_used={batch.retries_used} "
        f"cache_hit={batch.cache_hit} fetch_ms={batch.fetch_duration_ms} total_ms={batch.total_duration_ms}"
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
            "source_total_duration_ms": batch.source_total_duration_ms,
            "ingestion_duration_ms": batch.ingestion_duration_ms,
        },
    )
    print(
        "Smoke live research completed "
        f"run_id={batch.run_id} market_id={batch.market_id} sources={batch.source_count} "
        f"raw={batch.raw_count} normalized={batch.normalized_count} deduped={batch.deduplicated_count} "
        f"source_failures={batch.source_failures} retries_used={batch.retries_used} cache_hits={batch.cache_hits} "
        f"source_total_ms={batch.source_total_duration_ms} ingest_ms={batch.ingestion_duration_ms}"
    )
    if batch.source_failures >= batch.source_count:
        print("Smoke live research failed: all configured sources failed.")
        return 1
    return 0
