from __future__ import annotations

from datetime import UTC, datetime
from urllib import error

import pytest

from prediction_market_bot.infrastructure.alt_data import RedditOAuthAdapter, RedditQuery, RedditQueryKind
from prediction_market_bot.infrastructure.alt_data.adapters.reddit_oauth_adapter import RedditSourceFetchError


def test_reddit_oauth_adapter_requires_token() -> None:
    with pytest.raises(ValueError, match="oauth_token"):
        RedditOAuthAdapter(source_id="reddit", oauth_token="")


def test_reddit_oauth_adapter_normalizes_submission_and_respects_rate_limit_headers() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def fake_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del timeout
        assert headers.get("Authorization", "").startswith("Bearer ")
        calls.append(url)
        if "/r/worldnews/new" in url:
            return (
                {
                    "data": {
                        "after": None,
                        "children": [
                            {
                                "kind": "t3",
                                "data": {
                                    "id": "abc123",
                                    "name": "t3_abc123",
                                    "subreddit": "worldnews",
                                    "subreddit_id": "t5_worldnews",
                                    "title": "Headline",
                                    "selftext": "Body",
                                    "author": "alice",
                                    "score": 12,
                                    "num_comments": 2,
                                    "created_utc": 1767350400,
                                    "permalink": "/r/worldnews/comments/abc123/headline/",
                                    "url": "https://example.test/news",
                                },
                            }
                        ],
                    }
                },
                {
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": "1",
                    "x-ratelimit-used": "60",
                },
            )
        if "/r/worldnews/about" in url:
            return (
                {
                    "data": {
                        "id": "worldnews",
                        "name": "t5_worldnews",
                        "display_name": "worldnews",
                        "title": "World News",
                        "public_description": "desc",
                        "subscribers": 1000,
                        "url": "/r/worldnews/",
                        "over18": False,
                    }
                },
                {"x-ratelimit-remaining": "2", "x-ratelimit-reset": "5", "x-ratelimit-used": "58"},
            )
        raise AssertionError(f"Unexpected URL {url}")

    adapter = RedditOAuthAdapter(
        source_id="reddit",
        oauth_token="token-123",
        endpoint_url="https://oauth.reddit.com",
        timeout_sec=5.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_json_fn=fake_fetch,
        sleep_fn=sleeps.append,
        now_fn=lambda: datetime(2026, 1, 3, 0, 0, tzinfo=UTC),
    )

    page = adapter.fetch_submissions(RedditQuery(value="worldnews", kind=RedditQueryKind.SUBREDDIT), limit=10)

    assert len(calls) == 2
    assert page.retries_used == 0
    assert page.rate_limit_remaining == 0.0
    assert sleeps == [1.0]
    assert len(page.records) == 1
    row = page.records[0]
    assert row.evidence_kind.value == "submission"
    assert row.subreddit == "worldnews"
    assert row.post_id == "abc123"
    assert row.title == "Headline"
    assert row.permalink_url.startswith("https://www.reddit.com/r/worldnews/comments/abc123/")
    assert row.dedup_key.startswith("reddit:sha256:")


def test_reddit_oauth_adapter_retries_timeout() -> None:
    calls = 0

    def flaky_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del url, timeout, headers
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timeout")
        return (
            {"data": {"after": None, "children": []}},
            {"x-ratelimit-remaining": "10", "x-ratelimit-reset": "1", "x-ratelimit-used": "0"},
        )

    adapter = RedditOAuthAdapter(
        source_id="reddit",
        oauth_token="token-123",
        endpoint_url="https://oauth.reddit.com",
        timeout_sec=1.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_json_fn=flaky_fetch,
        sleep_fn=lambda _: None,
    )
    page = adapter.fetch_submissions(RedditQuery(value="worldnews", kind=RedditQueryKind.SUBREDDIT), limit=5)
    assert page.retries_used == 1
    assert calls == 2


def test_reddit_oauth_adapter_fails_on_http_401() -> None:
    def unauthorized_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del url, timeout, headers
        raise error.HTTPError(
            url="https://oauth.reddit.com/api/v1/me",
            code=401,
            msg="unauthorized",
            hdrs={},
            fp=None,
        )

    adapter = RedditOAuthAdapter(
        source_id="reddit",
        oauth_token="bad-token",
        endpoint_url="https://oauth.reddit.com",
        fetch_json_fn=unauthorized_fetch,
    )
    with pytest.raises(RedditSourceFetchError, match="reason=http_401"):
        adapter.verify_oauth()
