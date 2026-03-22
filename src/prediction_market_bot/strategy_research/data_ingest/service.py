from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Mapping

from .models import (
    BackfillSummary,
    DatasetInspection,
    DatasetVerification,
    HistoricalIngestCheckpoint,
    HistoricalMarketPage,
)
from .normalizer import (
    normalize_dataset_record_count,
    normalize_event,
    normalize_market,
    normalize_market_snapshot,
    normalize_orderbook_snapshot,
    normalize_resolution,
    normalize_trade,
)
from .provider import HistoricalResolvedMarketProvider
from .storage import DatasetLayout, DatasetStorage, NORMALIZED_FILES, RAW_FILES

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _Counters:
    pages_processed: int = 0
    markets_seen: int = 0
    markets_processed: int = 0
    markets_skipped: int = 0
    retries_used: int = 0


class HistoricalDataIngestionService:
    def __init__(
        self,
        *,
        base_dir: Path,
        provider: HistoricalResolvedMarketProvider,
        default_dataset_id: str,
        default_page_size: int,
        default_max_pages_per_run: int,
    ) -> None:
        self.base_dir = base_dir
        self.provider = provider
        self.default_dataset_id = default_dataset_id
        self.default_page_size = max(default_page_size, 1)
        self.default_max_pages_per_run = max(default_max_pages_per_run, 0)

    def backfill_historical_markets(
        self,
        *,
        dataset_id: str | None = None,
        page_size: int | None = None,
        max_pages: int | None = None,
        start_cursor: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> BackfillSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = DatasetLayout.from_base(base_dir=self.base_dir, dataset_id=effective_dataset_id, checkpoint_path=checkpoint_path)
        storage = DatasetStorage(layout)

        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(dataset_id=effective_dataset_id)
        if start_cursor is not None:
            checkpoint.cursor = start_cursor.strip() or None
            checkpoint.completed_at_utc = None

        started_at = datetime.now(UTC)
        counters = _Counters()
        warnings: list[str] = []
        cursor = checkpoint.cursor
        effective_max_pages = max_pages if max_pages is not None else self.default_max_pages_per_run
        effective_max_pages = max(int(effective_max_pages), 0)
        effective_page_size = max(int(page_size if page_size is not None else self.default_page_size), 1)

        while True:
            if effective_max_pages > 0 and counters.pages_processed >= effective_max_pages:
                break
            page = self.provider.fetch_resolved_markets_page(
                cursor=cursor,
                page_size=effective_page_size,
                date_from=date_from,
                date_to=date_to,
            )
            counters = _Counters(
                pages_processed=counters.pages_processed + 1,
                markets_seen=counters.markets_seen + len(page.markets),
                markets_processed=counters.markets_processed,
                markets_skipped=counters.markets_skipped,
                retries_used=counters.retries_used + page.retries_used,
            )

            checkpoint, processed_delta, skipped_delta = self._process_page(
                storage=storage,
                checkpoint=checkpoint,
                page=page,
                warnings=warnings,
            )
            counters = _Counters(
                pages_processed=counters.pages_processed,
                markets_seen=counters.markets_seen,
                markets_processed=len(checkpoint.processed_market_ids),
                markets_skipped=counters.markets_skipped + skipped_delta,
                retries_used=counters.retries_used,
            )
            cursor = page.next_cursor
            checkpoint.cursor = cursor
            checkpoint.updated_at_utc = datetime.now(UTC)
            if cursor is None:
                checkpoint.completed_at_utc = datetime.now(UTC)
            storage.save_checkpoint(checkpoint)
            if cursor is None:
                break

        finished_at = datetime.now(UTC)
        manifest = {
            "dataset_id": effective_dataset_id,
            "updated_at_utc": finished_at.isoformat(),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "next_cursor": checkpoint.cursor,
        }
        storage.write_manifest(manifest)
        logger.info(
            "historical_markets_backfill_completed",
            extra={
                "event": "historical_markets_backfill_completed",
                "dataset_id": effective_dataset_id,
                "pages_processed": counters.pages_processed,
                "markets_seen": counters.markets_seen,
                "markets_processed": counters.markets_processed,
                "markets_skipped": counters.markets_skipped,
                "retries_used": counters.retries_used,
                "checkpoint_completed": checkpoint.completed_at_utc is not None,
                "next_cursor": checkpoint.cursor or "",
            },
        )
        return BackfillSummary(
            dataset_id=effective_dataset_id,
            dataset_root=layout.root_dir,
            pages_processed=counters.pages_processed,
            markets_seen=counters.markets_seen,
            markets_processed=counters.markets_processed,
            markets_skipped=counters.markets_skipped,
            retries_used=counters.retries_used,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            next_cursor=checkpoint.cursor,
            warnings=tuple(warnings),
        )

    def inspect_dataset(self, *, dataset_id: str | None = None, checkpoint_path: Path | None = None) -> DatasetInspection:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = DatasetLayout.from_base(base_dir=self.base_dir, dataset_id=effective_dataset_id, checkpoint_path=checkpoint_path)
        storage = DatasetStorage(layout)
        checkpoint = storage.load_checkpoint(dataset_id=effective_dataset_id)
        raw_counts = {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES}
        normalized_counts = {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES}
        return DatasetInspection(
            dataset_id=effective_dataset_id,
            dataset_root=layout.root_dir,
            raw_counts=raw_counts,
            normalized_counts=normalized_counts,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            checkpoint_cursor=checkpoint.cursor,
            processed_market_count=len(checkpoint.processed_market_ids),
        )

    def verify_dataset(self, *, dataset_id: str | None = None, checkpoint_path: Path | None = None) -> DatasetVerification:
        inspection = self.inspect_dataset(dataset_id=dataset_id, checkpoint_path=checkpoint_path)
        layout = DatasetLayout.from_base(
            base_dir=self.base_dir,
            dataset_id=inspection.dataset_id,
            checkpoint_path=checkpoint_path,
        )
        storage = DatasetStorage(layout)
        errors: list[str] = []
        warnings: list[str] = []

        required_non_empty = ("markets", "resolutions")
        for name in required_non_empty:
            count = inspection.normalized_counts.get(name, 0)
            if count <= 0:
                errors.append(f"normalized/{name}.jsonl is empty")

        market_rows = storage.read_rows(normalized=True, name="markets")
        resolution_rows = storage.read_rows(normalized=True, name="resolutions")
        market_unique, market_dupes = normalize_dataset_record_count(market_rows, key_fields=("market_id",))
        resolution_unique, resolution_dupes = normalize_dataset_record_count(resolution_rows, key_fields=("market_id",))
        if market_dupes > 0:
            errors.append(f"duplicate normalized markets by market_id: {market_dupes}")
        if resolution_dupes > 0:
            errors.append(f"duplicate normalized resolutions by market_id: {resolution_dupes}")

        market_ids = {str(row.get("market_id") or "").strip() for row in market_rows if str(row.get("market_id") or "").strip()}
        resolution_ids = {
            str(row.get("market_id") or "").strip() for row in resolution_rows if str(row.get("market_id") or "").strip()
        }
        missing_resolution = sorted(market_ids - resolution_ids)
        if missing_resolution:
            errors.append(f"markets_without_resolution={len(missing_resolution)}")
        if not inspection.checkpoint_present:
            warnings.append("checkpoint file not found")
        elif not inspection.checkpoint_completed:
            warnings.append("checkpoint not completed (cursor still present)")

        ok = not errors
        details: dict[str, Any] = {
            "dataset_root": str(inspection.dataset_root),
            "raw_counts": dict(inspection.raw_counts),
            "normalized_counts": dict(inspection.normalized_counts),
            "checkpoint_path": str(inspection.checkpoint_path),
            "checkpoint_completed": inspection.checkpoint_completed,
            "checkpoint_cursor": inspection.checkpoint_cursor,
            "market_unique_count": market_unique,
            "resolution_unique_count": resolution_unique,
        }
        return DatasetVerification(
            dataset_id=inspection.dataset_id,
            ok=ok,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _process_page(
        self,
        *,
        storage: DatasetStorage,
        checkpoint: HistoricalIngestCheckpoint,
        page: HistoricalMarketPage,
        warnings: list[str],
    ) -> tuple[HistoricalIngestCheckpoint, int, int]:
        processed_delta = 0
        skipped_delta = 0
        for market_payload in page.markets:
            market_id = self._extract_market_id(market_payload)
            if not market_id:
                warnings.append("skipped market without market_id")
                skipped_delta += 1
                continue
            if market_id in checkpoint.processed_market_ids:
                skipped_delta += 1
                continue

            event_id = str(market_payload.get("event_id") or market_payload.get("eventId") or "").strip()
            event_payload = self.provider.fetch_event_metadata(event_id=event_id) if event_id else None
            if event_payload is not None:
                storage.append_raw("events", {"market_id": market_id, "payload": dict(event_payload)})
                storage.append_normalized("events", normalize_event(event_payload))

            storage.append_raw("markets", {"market_id": market_id, "payload": dict(market_payload)})
            storage.append_normalized("markets", normalize_market(market_payload, event_id=event_id))

            snapshots = self.provider.fetch_market_snapshots(market_id=market_id)
            for row in snapshots:
                storage.append_raw("market_snapshots", {"market_id": market_id, "payload": dict(row)})
                storage.append_normalized("market_snapshots", normalize_market_snapshot(row, market_id=market_id))

            orderbook_rows = self.provider.fetch_orderbook_snapshots(market_id=market_id)
            for row in orderbook_rows:
                storage.append_raw("orderbook_snapshots", {"market_id": market_id, "payload": dict(row)})
                storage.append_normalized("orderbook_snapshots", normalize_orderbook_snapshot(row, market_id=market_id))

            trade_rows = self.provider.fetch_trades(market_id=market_id)
            for row in trade_rows:
                storage.append_raw("trades", {"market_id": market_id, "payload": dict(row)})
                storage.append_normalized("trades", normalize_trade(row, market_id=market_id))

            resolution = self.provider.fetch_resolution(market_id=market_id, market_payload=market_payload)
            storage.append_raw("resolutions", {"market_id": market_id, "payload": dict(resolution)})
            storage.append_normalized("resolutions", normalize_resolution(resolution, market_id=market_id))
            checkpoint.processed_market_ids.add(market_id)
            processed_delta += 1
            checkpoint.updated_at_utc = datetime.now(UTC)
        return checkpoint, processed_delta, skipped_delta

    @staticmethod
    def _extract_market_id(payload: Mapping[str, Any]) -> str:
        for key in ("id", "market_id", "conditionId", "slug"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
