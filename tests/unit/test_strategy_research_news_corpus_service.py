from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import NewsArticleRecord, NewsFetchBatch, NewsQuery, NewsQueryKind
from prediction_market_bot.infrastructure.alt_data.adapters.rss_news_adapter import NewsSourceFetchError
from prediction_market_bot.strategy_research.news_corpus import NewsCorpusService


def _record(*, query: str, query_kind: NewsQueryKind, dedup_key: str, idx: int) -> NewsArticleRecord:
    return NewsArticleRecord(
        source_id="news_rss_web",
        source_class="news_rss_web",
        source_name="rss_news_adapter",
        query=query,
        query_kind=query_kind,
        dedup_key=dedup_key,
        source_record_id=f"rec-{idx}",
        title=f"title-{idx}",
        summary=f"summary-{idx}",
        article_url=f"https://example.test/article-{idx}",
        article_domain="example.test",
        publisher="Example",
        published_at_utc=datetime(2026, 1, idx, 10, 0, tzinfo=UTC),
        fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        source_metadata={"feed_title": "Google News"},
        raw_payload={"id": idx},
    )


class _FakeNewsAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, query: NewsQuery, *, limit: int = 50) -> NewsFetchBatch:
        del limit
        self.calls.append(query.key)
        if query.value.lower() == "broken":
            raise NewsSourceFetchError(source_id="news_rss_web", reason_code="fetch_failed", detail="TimeoutError")
        if query.kind == NewsQueryKind.TOPIC:
            records = (
                _record(query=query.value, query_kind=query.kind, dedup_key="dedup-a", idx=1),
                _record(query=query.value, query_kind=query.kind, dedup_key="dedup-a", idx=2),
            )
        else:
            records = (_record(query=query.value, query_kind=query.kind, dedup_key="dedup-b", idx=3),)
        return NewsFetchBatch(
            source_id="news_rss_web",
            source_name="rss_news_adapter",
            query=query.value,
            query_kind=query.kind,
            feed_url="https://news.google.com/rss/search?q=x",
            fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            retries_used=1,
            cache_hit=False,
            duration_ms=5.0,
            feed_metadata={"feed_title": "Google News"},
            records=records,
        )


def test_news_corpus_backfill_is_checkpointed_and_deduplicated(tmp_path: Path) -> None:
    adapter = _FakeNewsAdapter()
    service = NewsCorpusService(
        base_dir=tmp_path / "news-corpus",
        default_corpus_id="news-it",
        source_id="news_rss_web",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        throttle_sec=0.0,
    )

    first = service.backfill_news(
        topics=("WORLD",),
        keywords=("inflation",),
    )
    assert first.query_count == 2
    assert first.queries_processed == 2
    assert first.source_failures == 0
    assert first.raw_rows_persisted == 2
    assert first.normalized_rows_persisted == 2
    assert first.normalized_rows_deduped == 1
    assert adapter.calls == ["topic:world", "keyword:inflation"]

    second = service.backfill_news(
        topics=("WORLD",),
        keywords=("inflation",),
    )
    assert second.queries_processed == 0
    assert second.queries_skipped == 2
    assert adapter.calls == ["topic:world", "keyword:inflation"]

    inspection = service.inspect_corpus()
    assert inspection.raw_counts["news_payloads"] == 2
    assert inspection.normalized_counts["news_articles"] == 2

    verification = service.verify_corpus()
    assert verification.ok is True


def test_news_corpus_backfill_tracks_source_failures(tmp_path: Path) -> None:
    adapter = _FakeNewsAdapter()
    service = NewsCorpusService(
        base_dir=tmp_path / "news-corpus",
        default_corpus_id="news-it",
        source_id="news_rss_web",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        throttle_sec=0.0,
    )

    summary = service.backfill_news(
        topics=("WORLD",),
        keywords=("broken",),
    )
    assert summary.query_count == 2
    assert summary.queries_processed == 1
    assert summary.source_failures == 1
    assert any("fetch_failed" in warning for warning in summary.warnings)
