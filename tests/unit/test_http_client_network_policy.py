from __future__ import annotations

import pytest

from prediction_market_bot.infrastructure.http_client import HttpClientError, StructuredHttpClient


def test_http_client_blocks_host_outside_allowlist() -> None:
    client = StructuredHttpClient(
        user_agent="test-agent",
        enforce_allowed_hosts=True,
        allowed_hosts=("allowed.example",),
    )
    with pytest.raises(HttpClientError) as exc:
        client.fetch_json(source="test", url="https://blocked.example/data")
    assert exc.value.metadata.reason_code == "host_not_allowed"


def test_http_client_blocks_unsupported_scheme() -> None:
    client = StructuredHttpClient(user_agent="test-agent")
    with pytest.raises(HttpClientError) as exc:
        client.fetch_json(source="test", url="ftp://example.com/data")
    assert exc.value.metadata.reason_code == "unsupported_url_scheme"


def test_http_client_enforces_max_response_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        status = 200

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            del exc_type, exc, tb
            return False

        def read(self, amount: int = -1) -> bytes:
            payload = b'{"value":"' + (b"x" * 256) + b'"}'
            if amount < 0:
                return payload
            return payload[:amount]

    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.http_client.request.urlopen",
        lambda req, timeout: _FakeResponse(),
    )
    client = StructuredHttpClient(user_agent="test-agent", max_response_bytes=32)
    with pytest.raises(HttpClientError) as exc:
        client.fetch_json(source="test", url="https://allowed.example/data")
    assert exc.value.metadata.reason_code == "response_body_limit_exceeded"
