from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class HistoricalDataIngestSettings:
    base_dir: str = "data/strategy-research"
    default_dataset_id: str = "historical-markets"
    markets_endpoint_url: str = "https://gamma-api.polymarket.com/markets"
    events_endpoint_url: str = "https://gamma-api.polymarket.com/events/{event_id}"
    snapshots_endpoint_url: str = ""
    orderbook_endpoint_url: str = ""
    trades_endpoint_url: str = ""
    resolutions_endpoint_url: str = ""
    page_size: int = 200
    max_pages_per_run: int = 0
    throttle_sec: float = 0.2
    include_orderbook: bool = True
    include_trades: bool = True


@dataclass(slots=True, frozen=True)
class HistoricalMarketPage:
    markets: tuple[Mapping[str, Any], ...]
    next_cursor: str | None
    retries_used: int = 0


@dataclass(slots=True)
class HistoricalIngestCheckpoint:
    dataset_id: str
    cursor: str | None = None
    processed_market_ids: set[str] = field(default_factory=set)
    started_at_utc: datetime = field(default_factory=utc_now)
    updated_at_utc: datetime = field(default_factory=utc_now)
    completed_at_utc: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "cursor": self.cursor,
            "processed_market_ids": sorted(self.processed_market_ids),
            "started_at_utc": self.started_at_utc.isoformat(),
            "updated_at_utc": self.updated_at_utc.isoformat(),
            "completed_at_utc": self.completed_at_utc.isoformat() if self.completed_at_utc is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, default_dataset_id: str) -> "HistoricalIngestCheckpoint":
        dataset_id = str(payload.get("dataset_id") or default_dataset_id).strip() or default_dataset_id
        cursor_raw = payload.get("cursor")
        cursor = str(cursor_raw).strip() if isinstance(cursor_raw, str) and cursor_raw.strip() else None
        processed_raw = payload.get("processed_market_ids")
        processed: set[str] = set()
        if isinstance(processed_raw, list):
            for row in processed_raw:
                if isinstance(row, str) and row.strip():
                    processed.add(row.strip())

        def _parse_dt(value: Any, fallback: datetime) -> datetime:
            if not isinstance(value, str) or not value.strip():
                return fallback
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return fallback
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)

        started_fallback = utc_now()
        started = _parse_dt(payload.get("started_at_utc"), started_fallback)
        updated = _parse_dt(payload.get("updated_at_utc"), started)
        completed_raw = payload.get("completed_at_utc")
        completed = _parse_dt(completed_raw, started) if isinstance(completed_raw, str) and completed_raw.strip() else None
        return cls(
            dataset_id=dataset_id,
            cursor=cursor,
            processed_market_ids=processed,
            started_at_utc=started,
            updated_at_utc=updated,
            completed_at_utc=completed,
        )


@dataclass(slots=True, frozen=True)
class BackfillSummary:
    dataset_id: str
    dataset_root: Path
    pages_processed: int
    markets_seen: int
    markets_processed: int
    markets_skipped: int
    retries_used: int
    started_at_utc: datetime
    finished_at_utc: datetime
    checkpoint_path: Path
    checkpoint_completed: bool
    next_cursor: str | None
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_root": str(self.dataset_root),
            "pages_processed": self.pages_processed,
            "markets_seen": self.markets_seen,
            "markets_processed": self.markets_processed,
            "markets_skipped": self.markets_skipped,
            "retries_used": self.retries_used,
            "started_at_utc": self.started_at_utc.isoformat(),
            "finished_at_utc": self.finished_at_utc.isoformat(),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_completed": self.checkpoint_completed,
            "next_cursor": self.next_cursor,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class DatasetInspection:
    dataset_id: str
    dataset_root: Path
    raw_counts: Mapping[str, int]
    normalized_counts: Mapping[str, int]
    checkpoint_path: Path
    checkpoint_present: bool
    checkpoint_completed: bool
    checkpoint_cursor: str | None
    processed_market_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_root": str(self.dataset_root),
            "raw_counts": dict(self.raw_counts),
            "normalized_counts": dict(self.normalized_counts),
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_present": self.checkpoint_present,
            "checkpoint_completed": self.checkpoint_completed,
            "checkpoint_cursor": self.checkpoint_cursor,
            "processed_market_count": self.processed_market_count,
        }


@dataclass(slots=True, frozen=True)
class DatasetVerification:
    dataset_id: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }

