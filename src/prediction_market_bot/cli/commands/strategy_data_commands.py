from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_http_client
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.strategy_research.data_ingest import (
    HistoricalDataIngestionService,
    HistoricalResolvedMarketProvider,
)

logger = logging.getLogger(__name__)


def _build_historical_service(config_path: Path, agents_config_path: Path) -> HistoricalDataIngestionService:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    sr = settings.strategy_research
    http_client = build_http_client(settings)
    provider = HistoricalResolvedMarketProvider(
        http_client=http_client,
        markets_endpoint_url=sr.markets_endpoint_url,
        events_endpoint_url=sr.events_endpoint_url,
        snapshots_endpoint_url=sr.snapshots_endpoint_url,
        orderbook_endpoint_url=sr.orderbook_endpoint_url,
        trades_endpoint_url=sr.trades_endpoint_url,
        resolutions_endpoint_url=sr.resolutions_endpoint_url,
        include_orderbook=sr.include_orderbook,
        include_trades=sr.include_trades,
        page_size=sr.page_size,
        throttle_sec=sr.throttle_sec,
    )
    return HistoricalDataIngestionService(
        base_dir=Path(sr.base_dir),
        provider=provider,
        default_dataset_id=sr.default_dataset_id,
        default_page_size=sr.page_size,
        default_max_pages_per_run=sr.max_pages_per_run,
    )


def backfill_historical_markets_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    page_size: int | None,
    max_pages: int | None,
    start_cursor: str | None,
    checkpoint_path: Path | None,
    reset_checkpoint: bool,
    date_from: date | None,
    date_to: date | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    summary = service.backfill_historical_markets(
        dataset_id=dataset_id,
        page_size=page_size,
        max_pages=max_pages,
        start_cursor=start_cursor,
        checkpoint_path=checkpoint_path,
        reset_checkpoint=reset_checkpoint,
        date_from=date_from,
        date_to=date_to,
    )
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Historical backfill completed "
            f"dataset_id={summary.dataset_id} pages={summary.pages_processed} "
            f"markets_processed={summary.markets_processed} markets_skipped={summary.markets_skipped} "
            f"checkpoint_completed={summary.checkpoint_completed} next_cursor={summary.next_cursor or 'none'}"
        )
        print(f"Dataset root: {summary.dataset_root}")
        print(f"Checkpoint: {summary.checkpoint_path}")
        if summary.warnings:
            print("Warnings:")
            for warning in summary.warnings:
                print(f"- {warning}")
    logger.info(
        "historical_markets_backfill_command_completed",
        extra={
            "event": "historical_markets_backfill_command_completed",
            **payload,
        },
    )
    return 0


def inspect_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    inspection = service.inspect_dataset(dataset_id=dataset_id, checkpoint_path=checkpoint_path)
    payload = inspection.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Dataset: {inspection.dataset_id}")
        print(f"Root: {inspection.dataset_root}")
        print(f"Checkpoint: {inspection.checkpoint_path}")
        print(f"Checkpoint present: {inspection.checkpoint_present}")
        print(f"Checkpoint completed: {inspection.checkpoint_completed}")
        print(f"Next cursor: {inspection.checkpoint_cursor or 'none'}")
        print(f"Processed markets: {inspection.processed_market_count}")
        print("Raw counts:")
        for key, value in inspection.raw_counts.items():
            print(f"- {key}: {value}")
        print("Normalized counts:")
        for key, value in inspection.normalized_counts.items():
            print(f"- {key}: {value}")
    logger.info(
        "historical_dataset_inspected",
        extra={
            "event": "historical_dataset_inspected",
            **payload,
        },
    )
    return 0


def verify_dataset_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None,
    checkpoint_path: Path | None,
    as_json: bool,
) -> int:
    service = _build_historical_service(config_path, agents_config_path)
    verification = service.verify_dataset(dataset_id=dataset_id, checkpoint_path=checkpoint_path)
    payload = verification.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        status = "OK" if verification.ok else "FAIL"
        print(f"Dataset verification: {status}")
        for error in verification.errors:
            print(f"ERROR: {error}")
        for warning in verification.warnings:
            print(f"WARN: {warning}")
    logger.info(
        "historical_dataset_verified",
        extra={
            "event": "historical_dataset_verified",
            **payload,
        },
    )
    return 0 if verification.ok else 1
