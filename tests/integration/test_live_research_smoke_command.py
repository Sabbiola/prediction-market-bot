from __future__ import annotations

import json
from pathlib import Path

import pytest

from prediction_market_bot.infrastructure import http_client as http_client_module
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


def test_smoke_live_research_command_fetches_and_persists_batch(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 100,
                    "title": "Inflation outlook",
                    "snippet": "Inflation decline forecast for next quarter.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W111",
                "display_name": "Macro inflation survey",
                "publication_date": "2026-02-15",
                "primary_location": {
                    "landing_page_url": "https://example.org/research/111",
                    "source": {"display_name": "Macro Journal"},
                },
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    exit_code = main(
        [
            "smoke-live-research",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--market-id",
            "market-smoke-1",
            "--title",
            "Will inflation decline in the next quarter?",
            "--query",
            "inflation decline",
            "--wikipedia-endpoint",
            "https://wikipedia.test/api.php",
            "--openalex-endpoint",
            "https://openalex.test/works",
            "--retries",
            "0",
            "--limit-per-source",
            "5",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Smoke live research completed" in output

    artifacts_dir = app_cfg.parent / "artifacts"
    raw_path = artifacts_dir / "raw_research_findings.jsonl"
    normalized_path = artifacts_dir / "normalized_research_findings.jsonl"
    deduped_path = artifacts_dir / "deduped_research_findings.jsonl"
    assert raw_path.exists()
    assert normalized_path.exists()
    assert deduped_path.exists()

    raw_rows = _read_jsonl(raw_path)
    normalized_rows = _read_jsonl(normalized_path)
    dedup_rows = _read_jsonl(deduped_path)
    assert any(row.get("run_id") == deterministic_run_id for row in raw_rows)
    assert any(row.get("run_id") == deterministic_run_id for row in normalized_rows)
    assert any(row.get("run_id") == deterministic_run_id for row in dedup_rows)

    audit_rows = _read_jsonl(app_cfg.parent / "audit" / "events.jsonl")
    assert any(
        row.get("run_id") == deterministic_run_id and row.get("event_type") == "research_ingestion_end"
        for row in audit_rows
    )


def test_smoke_live_research_command_fails_when_all_sources_fail(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps({"query": {"unexpected": []}}))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps({"unexpected": []}))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    exit_code = main(
        [
            "smoke-live-research",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--market-id",
            "market-smoke-2",
            "--title",
            "Will inflation decline in the next quarter?",
            "--query",
            "inflation decline",
            "--wikipedia-endpoint",
            "https://wikipedia.test/api.php",
            "--openalex-endpoint",
            "https://openalex.test/works",
            "--retries",
            "0",
            "--limit-per-source",
            "5",
        ]
    )
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Smoke live research completed" in output
    assert "Smoke live research failed: all configured sources failed." in output
