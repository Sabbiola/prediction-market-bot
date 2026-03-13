from __future__ import annotations

import json
from pathlib import Path

import pytest

from prediction_market_bot.infrastructure import live_market_data as live_market_data_module
from prediction_market_bot.main import main


class _MockHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_MockHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_smoke_live_data_command_fetches_and_persists_batch(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    payload = [
        {
            "id": "live-smoke-1",
            "question": "Will smoke test pass?",
            "active": True,
            "closed": False,
            "resolved": False,
            "updatedAt": "2026-03-13T11:55:00Z",
            "endDate": "2026-03-14T11:55:00Z",
            "outcomePrices": "[\"0.55\", \"0.45\"]",
            "liquidity": "42000",
            "volume24hr": "18000",
            "bestBid": "0.54",
            "bestAsk": "0.56",
            "priceChange24h": "0.005",
            "category": "testing",
        }
    ]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(json.dumps(payload))

    monkeypatch.setattr(live_market_data_module.request, "urlopen", fake_urlopen)

    exit_code = main(
        [
            "smoke-live-data",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--endpoint",
            "https://example.test/markets",
            "--retries",
            "0",
            "--max-staleness-sec",
            "999999",
            "--limit",
            "10",
        ]
    )
    assert exit_code == 0

    artifacts_dir = app_cfg.parent / "artifacts"
    raw_path = artifacts_dir / "raw_market_snapshots.jsonl"
    normalized_path = artifacts_dir / "normalized_market_snapshots.jsonl"
    assert raw_path.exists()
    assert normalized_path.exists()

    raw_rows = _read_jsonl(raw_path)
    normalized_rows = _read_jsonl(normalized_path)
    assert any(row.get("run_id") == deterministic_run_id for row in raw_rows)
    assert any(row.get("run_id") == deterministic_run_id for row in normalized_rows)

    audit_rows = _read_jsonl(app_cfg.parent / "audit" / "events.jsonl")
    assert any(
        row.get("run_id") == deterministic_run_id and row.get("event_type") == "live_market_fetch_end"
        for row in audit_rows
    )
