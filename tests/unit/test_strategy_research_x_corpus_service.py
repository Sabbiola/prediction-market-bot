from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import XFetchPage, XPostRecord, XQuery
from prediction_market_bot.strategy_research.x_corpus import XCorpusService


def _record(*, query: XQuery, dedup_key: str, post_id: str, idx: int) -> XPostRecord:
    return XPostRecord(
        source_id="x",
        source_class="x",
        source_name="x_api_adapter",
        query=query.value,
        query_kind=query.kind,
        dedup_key=dedup_key,
        source_record_id=post_id,
        post_id=post_id,
        post_url=f"https://x.com/analyst/status/{post_id}",
        author_id="user-1",
        author_username="analyst",
        text=f"post-{idx}",
        lang="en",
        conversation_id=post_id,
        public_metrics={"like_count": idx},
        created_at_utc=datetime(2026, 1, idx, 10, 0, tzinfo=UTC),
        fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        source_metadata={"query_kind": query.kind.value, "auth_mode": "user_context"},
        raw_payload={"id": post_id},
    )


class _FakeXAdapter:
    auth_mode = "user_context"
    search_endpoint_available = True
    account_endpoints_available = True

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str | None]] = []
        self.account_calls: list[tuple[str, str | None]] = []

    def fetch_search(self, query: XQuery, *, limit: int = 50, cursor: str | None = None) -> XFetchPage:
        del limit
        self.search_calls.append((query.key, cursor))
        records = (
            _record(query=query, dedup_key="x:sha256:dup-key", post_id="100", idx=1),
            _record(query=query, dedup_key="x:sha256:dup-key", post_id="101", idx=2),
        )
        return XFetchPage(
            source_id="x",
            source_name="x_api_adapter",
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            retries_used=1,
            duration_ms=2.0,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=9.0,
            rate_limit_reset_epoch=1767350460.0,
            records=records,
        )

    def fetch_account_posts(self, query: XQuery, *, limit: int = 50, cursor: str | None = None) -> XFetchPage:
        del limit
        self.account_calls.append((query.key, cursor))
        record = _record(query=query, dedup_key=f"x:sha256:{query.value}", post_id="200", idx=3)
        return XFetchPage(
            source_id="x",
            source_name="x_api_adapter",
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=1.0,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=8.0,
            rate_limit_reset_epoch=1767350500.0,
            records=(record,),
        )


def test_x_corpus_backfill_is_checkpointed_and_deduplicated(tmp_path: Path) -> None:
    adapter = _FakeXAdapter()
    service = XCorpusService(
        base_dir=tmp_path / "x-corpus",
        default_corpus_id="x-it",
        source_id="x",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        default_max_pages_per_query=1,
        throttle_sec=0.0,
    )

    first = service.backfill_x(accounts=("analyst",), keywords=("macro",))
    assert first.query_count == 2
    assert first.queries_processed == 2
    assert first.source_failures == 0
    assert first.raw_rows_persisted == 2
    assert first.normalized_rows_persisted == 2
    assert first.normalized_rows_deduped == 1
    assert adapter.search_calls == [("keyword:macro", None)]
    assert adapter.account_calls == [("account:analyst", None)]

    second = service.backfill_x(accounts=("analyst",), keywords=("macro",))
    assert second.queries_processed == 0
    assert second.queries_skipped == 2

    inspection = service.inspect_corpus()
    assert inspection.raw_counts["x_payloads"] == 2
    assert inspection.normalized_counts["x_evidence"] == 2

    verification = service.verify_corpus()
    assert verification.ok is True


def test_x_corpus_backfill_fails_clearly_when_account_query_uses_bearer_mode(tmp_path: Path) -> None:
    adapter = _FakeXAdapter()
    adapter.auth_mode = "bearer"
    service = XCorpusService(
        base_dir=tmp_path / "x-corpus",
        default_corpus_id="x-it",
        source_id="x",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        default_max_pages_per_query=1,
        throttle_sec=0.0,
    )

    summary = service.backfill_x(accounts=("analyst",))
    assert summary.query_count == 1
    assert summary.queries_processed == 0
    assert summary.source_failures == 1
    assert any("auth_mode_insufficient" in warning for warning in summary.warnings)
