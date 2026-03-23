from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prediction_market_bot.infrastructure.alt_data import XApiAdapter, XQuery, XQueryKind
from prediction_market_bot.infrastructure.alt_data.adapters.x_api_adapter import XSourceFetchError


def test_x_api_adapter_normalizes_search_records_and_parses_rate_limit_headers() -> None:
    calls: list[str] = []

    def fake_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del timeout
        calls.append(url)
        assert headers.get("Authorization", "").startswith("Bearer ")
        return (
            {
                "data": [
                    {
                        "id": "1890001",
                        "author_id": "user-1",
                        "text": "Policy rumor update",
                        "lang": "en",
                        "conversation_id": "1890001",
                        "created_at": "2026-01-02T10:00:00Z",
                        "public_metrics": {
                            "like_count": 4,
                            "retweet_count": 1,
                        },
                    }
                ],
                "includes": {
                    "users": [
                        {
                            "id": "user-1",
                            "username": "analyst",
                        }
                    ]
                },
                "meta": {
                    "next_token": "cursor-2",
                },
            },
            {
                "x-rate-limit-remaining": "9",
                "x-rate-limit-reset": "1767350460",
            },
        )

    adapter = XApiAdapter(
        source_id="x",
        auth_token="token-123",
        auth_mode="bearer",
        endpoint_url="https://api.x.com",
        timeout_sec=5.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_json_fn=fake_fetch,
        now_fn=lambda: datetime(2026, 1, 3, 0, 0, tzinfo=UTC),
    )

    page = adapter.fetch_search(XQuery(value="policy", kind=XQueryKind.KEYWORD), limit=25)

    assert len(calls) == 1
    assert page.query_kind == XQueryKind.KEYWORD
    assert page.next_cursor == "cursor-2"
    assert page.rate_limit_remaining == 9.0
    assert page.rate_limit_reset_epoch == 1767350460.0
    assert len(page.records) == 1
    row = page.records[0]
    assert row.post_id == "1890001"
    assert row.author_username == "analyst"
    assert row.post_url == "https://x.com/analyst/status/1890001"
    assert row.dedup_key.startswith("x:sha256:")
    assert row.created_at_utc is not None
    assert row.created_at_utc.isoformat() == "2026-01-02T10:00:00+00:00"


def test_x_api_adapter_fetch_account_posts_resolves_user_and_normalizes_records() -> None:
    calls: list[str] = []

    def fake_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del timeout, headers
        calls.append(url)
        if "/2/users/by/username/analyst" in url:
            return (
                {
                    "data": {
                        "id": "42",
                        "username": "analyst",
                    }
                },
                {},
            )
        if "/2/users/42/tweets" in url:
            return (
                {
                    "data": [
                        {
                            "id": "199",
                            "author_id": "42",
                            "text": "Account post",
                            "lang": "en",
                            "conversation_id": "199",
                            "created_at": "2026-01-02T11:00:00Z",
                            "public_metrics": {"like_count": 2},
                        }
                    ],
                    "meta": {"pagination_token": None},
                },
                {"x-rate-limit-remaining": "7", "x-rate-limit-reset": "1767350500"},
            )
        raise AssertionError(f"Unexpected URL: {url}")

    adapter = XApiAdapter(
        source_id="x",
        auth_token="token-123",
        auth_mode="user_context",
        endpoint_url="https://api.x.com",
        timeout_sec=5.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_json_fn=fake_fetch,
        now_fn=lambda: datetime(2026, 1, 3, 0, 0, tzinfo=UTC),
    )

    page = adapter.fetch_account_posts(XQuery(value="analyst", kind=XQueryKind.ACCOUNT), limit=20)

    assert len(calls) == 2
    assert page.query_kind == XQueryKind.ACCOUNT
    assert page.rate_limit_remaining == 7.0
    assert len(page.records) == 1
    row = page.records[0]
    assert row.author_id == "42"
    assert row.author_username == "analyst"
    assert row.post_url == "https://x.com/analyst/status/199"


def test_x_api_adapter_retries_timeout_then_succeeds() -> None:
    calls = 0

    def flaky_fetch(url: str, timeout: float, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
        del url, timeout, headers
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timeout")
        return ({"data": [], "meta": {}}, {})

    adapter = XApiAdapter(
        source_id="x",
        auth_token="token-123",
        auth_mode="bearer",
        endpoint_url="https://api.x.com",
        timeout_sec=1.0,
        max_retries=1,
        retry_backoff_sec=0.0,
        retry_jitter_sec=0.0,
        fetch_json_fn=flaky_fetch,
        sleep_fn=lambda _: None,
    )

    page = adapter.fetch_search(XQuery(value="macro", kind=XQueryKind.KEYWORD), limit=5)
    assert page.retries_used == 1
    assert calls == 2


def test_x_api_adapter_fails_clearly_when_search_endpoint_is_missing() -> None:
    adapter = XApiAdapter(
        source_id="x",
        auth_token="token-123",
        auth_mode="bearer",
        endpoint_url="https://api.x.com",
        search_endpoint_path="",
        fetch_json_fn=lambda *_: ({}, {}),
    )

    with pytest.raises(XSourceFetchError, match="reason=search_endpoint_unavailable"):
        adapter.fetch_search(XQuery(value="macro", kind=XQueryKind.KEYWORD), limit=5)
