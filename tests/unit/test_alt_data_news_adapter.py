from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prediction_market_bot.infrastructure.alt_data import GoogleNewsRssAdapter, NewsQuery, NewsQueryKind
from prediction_market_bot.infrastructure.alt_data.adapters.rss_news_adapter import NewsSourceFetchError


def test_google_news_rss_adapter_normalizes_records_and_uses_cache() -> None:
    calls: list[str] = []
    rss_payload = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News - Markets</title>
    <item>
      <title>Fed decision impacts rates</title>
      <link>https://example.test/fed-rates</link>
      <pubDate>Tue, 02 Jan 2026 10:00:00 GMT</pubDate>
      <source url="https://example.test">Example News</source>
      <description>Rates outlook changed after announcement.</description>
      <guid>item-1</guid>
    </item>
  </channel>
</rss>
"""

    def fake_fetch(url: str, timeout: float, headers: dict[str, str]) -> str:
        calls.append(url)
        assert timeout == 5.0
        assert "User-Agent" in headers
        return rss_payload

    adapter = GoogleNewsRssAdapter(
        source_id="news_rss_web",
        source_class="news_rss_web",
        endpoint_url="https://news.google.com/rss",
        timeout_sec=5.0,
        max_retries=1,
        cache_ttl_sec=60,
        fetch_text_fn=fake_fetch,
        now_fn=lambda: datetime(2026, 1, 3, 0, 0, tzinfo=UTC),
    )
    query = NewsQuery(value="federal reserve", kind=NewsQueryKind.KEYWORD)

    first_batch = adapter.fetch(query, limit=10)
    second_batch = adapter.fetch(query, limit=10)

    assert len(calls) == 1
    assert first_batch.cache_hit is False
    assert second_batch.cache_hit is True
    assert len(first_batch.records) == 1
    row = first_batch.records[0]
    assert row.title == "Fed decision impacts rates"
    assert row.article_url == "https://example.test/fed-rates"
    assert row.publisher == "Example News"
    assert row.query_kind == NewsQueryKind.KEYWORD
    assert row.published_at_utc is not None
    assert row.published_at_utc.isoformat() == "2026-01-02T10:00:00+00:00"
    assert row.dedup_key.startswith("news:sha256:")


def test_google_news_rss_adapter_retries_and_fails_clearly() -> None:
    calls = 0

    def failing_fetch(url: str, timeout: float, headers: dict[str, str]) -> str:
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    adapter = GoogleNewsRssAdapter(
        source_id="news_rss_web",
        endpoint_url="https://news.google.com/rss",
        timeout_sec=1.0,
        max_retries=2,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_text_fn=failing_fetch,
        sleep_fn=lambda _: None,
    )

    with pytest.raises(NewsSourceFetchError, match="reason=fetch_failed"):
        adapter.fetch(NewsQuery(value="WORLD", kind=NewsQueryKind.TOPIC), limit=5)
    assert calls == 3


def test_google_news_rss_adapter_rejects_invalid_xml() -> None:
    adapter = GoogleNewsRssAdapter(
        source_id="news_rss_web",
        endpoint_url="https://news.google.com/rss",
        fetch_text_fn=lambda *_: "<rss><channel><item>",
    )

    with pytest.raises(NewsSourceFetchError, match="reason=invalid_rss_xml"):
        adapter.fetch(NewsQuery(value="WORLD", kind=NewsQueryKind.TOPIC), limit=5)
