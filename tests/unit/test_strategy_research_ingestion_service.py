from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.strategy_research.data_ingest.models import HistoricalMarketPage
from prediction_market_bot.strategy_research.data_ingest.service import HistoricalDataIngestionService


@dataclass(slots=True)
class _FakeProvider:
    pages: list[HistoricalMarketPage]
    page_idx: int = 0
    market_fetch_calls: int = 0

    def fetch_resolved_markets_page(
        self,
        *,
        cursor: str | None,
        page_size: int | None,
        date_from: date | None,
        date_to: date | None,
    ) -> HistoricalMarketPage:
        del cursor, page_size, date_from, date_to
        if self.page_idx >= len(self.pages):
            return HistoricalMarketPage(markets=(), next_cursor=None, retries_used=0)
        page = self.pages[self.page_idx]
        self.page_idx += 1
        return page

    def fetch_event_metadata(self, *, event_id: str) -> Mapping[str, Any] | None:
        return {"id": event_id, "title": f"event-{event_id}", "category": "test"}

    def fetch_market_snapshots(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        self.market_fetch_calls += 1
        return ({"timestamp": "2026-01-01T00:00:00Z", "outcomePrices": [0.5, 0.5]},)

    def fetch_orderbook_snapshots(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        del market_id
        return ({"timestamp": "2026-01-01T00:00:00Z", "bestBid": "0.49", "bestAsk": "0.51"},)

    def fetch_trades(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        del market_id
        return ({"id": "trade-1", "timestamp": "2026-01-01T00:00:00Z", "price": "0.5", "size": "10"},)

    def fetch_resolution(self, *, market_id: str, market_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        del market_id
        return {
            "outcome": market_payload.get("resolution", "YES"),
            "resolutionDate": "2026-01-02T00:00:00Z",
            "status": "RESOLVED",
        }


def _build_service(tmp_path: Path, provider: _FakeProvider) -> HistoricalDataIngestionService:
    return HistoricalDataIngestionService(
        base_dir=tmp_path / "data-lake",
        provider=provider,  # type: ignore[arg-type]
        default_dataset_id="ds-test",
        default_page_size=100,
        default_max_pages_per_run=0,
    )


def test_backfill_resumable_checkpoint(tmp_path: Path) -> None:
    pages = [
        HistoricalMarketPage(
            markets=(
                {"id": "m1", "event_id": "e1", "question": "q1", "resolution": "YES"},
                {"id": "m2", "event_id": "e2", "question": "q2", "resolution": "NO"},
            ),
            next_cursor="cursor-2",
            retries_used=1,
        ),
        HistoricalMarketPage(
            markets=(
                {"id": "m3", "event_id": "e3", "question": "q3", "resolution": "YES"},
            ),
            next_cursor=None,
            retries_used=0,
        ),
    ]
    provider = _FakeProvider(pages=pages)
    service = _build_service(tmp_path, provider)

    first = service.backfill_historical_markets(max_pages=1)
    assert first.pages_processed == 1
    assert first.markets_processed == 2
    assert first.checkpoint_completed is False
    assert first.next_cursor == "cursor-2"

    second = service.backfill_historical_markets()
    assert second.pages_processed == 1
    assert second.markets_processed == 3
    assert second.checkpoint_completed is True
    assert second.next_cursor is None


def test_backfill_idempotent_rerun_skips_already_processed(tmp_path: Path) -> None:
    pages = [
        HistoricalMarketPage(
            markets=(
                {"id": "m1", "event_id": "e1", "question": "q1", "resolution": "YES"},
                {"id": "m2", "event_id": "e2", "question": "q2", "resolution": "NO"},
            ),
            next_cursor=None,
            retries_used=0,
        )
    ]
    provider = _FakeProvider(pages=pages)
    service = _build_service(tmp_path, provider)

    first = service.backfill_historical_markets()
    assert first.markets_processed == 2
    assert provider.market_fetch_calls == 2

    provider.page_idx = 0
    second = service.backfill_historical_markets()
    assert second.markets_processed == 2
    assert second.markets_skipped >= 2
    assert provider.market_fetch_calls == 2

