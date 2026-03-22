from __future__ import annotations

import json
from pathlib import Path
from urllib import error

import pytest
import yaml

from prediction_market_bot.infrastructure import http_client as http_client_module
from prediction_market_bot.main import main


class _MockHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_MockHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            return self._payload
        return self._payload[:amount]


def _read_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise AssertionError("Expected YAML mapping")
    return dict(raw)


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _set_runtime_mode(
    app_cfg_path: Path,
    *,
    runtime_mode: str,
    failure_policy: str = "FAIL_FAST",
    market_endpoint: str = "https://example.test/markets",
    wikipedia_endpoint: str = "https://wikipedia.test/api.php",
    openalex_endpoint: str = "https://openalex.test/works",
) -> None:
    app_cfg = _read_yaml(app_cfg_path)
    runtime = app_cfg.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        raise AssertionError("runtime section must be a mapping")
    runtime["mode"] = runtime_mode
    runtime["market_data_provider"] = "AUTO"
    runtime["research_provider"] = "AUTO"
    runtime["provider_failure_policy"] = failure_policy

    live_market_data = app_cfg.setdefault("live_market_data", {})
    if not isinstance(live_market_data, dict):
        raise AssertionError("live_market_data section must be a mapping")
    live_market_data["endpoint_url"] = market_endpoint
    live_market_data["max_staleness_sec"] = 999_999
    live_market_data["limit"] = 10

    live_research = app_cfg.setdefault("live_research", {})
    if not isinstance(live_research, dict):
        raise AssertionError("live_research section must be a mapping")
    live_research["limit_per_source"] = 5
    wikipedia = live_research.setdefault("wikipedia", {})
    if not isinstance(wikipedia, dict):
        raise AssertionError("live_research.wikipedia section must be a mapping")
    wikipedia["endpoint_url"] = wikipedia_endpoint
    openalex = live_research.setdefault("openalex", {})
    if not isinstance(openalex, dict):
        raise AssertionError("live_research.openalex section must be a mapping")
    openalex["endpoint_url"] = openalex_endpoint
    _write_yaml(app_cfg_path, app_cfg)


def test_run_once_uses_static_providers_in_dry_run_static_mode(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_runtime_mode(app_cfg, runtime_mode="DRY_RUN_STATIC")

    exit_code = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert exit_code == 0

    artifacts_dir = app_cfg.parent / "artifacts"
    market_candidates = _read_jsonl(artifacts_dir / "market_candidates.jsonl")
    assert any(row.get("run_id") == deterministic_run_id for row in market_candidates)

    raw_market_snapshots = _read_jsonl(artifacts_dir / "raw_market_snapshots.jsonl")
    raw_research_findings = _read_jsonl(artifacts_dir / "raw_research_findings.jsonl")
    assert not any(row.get("run_id") == deterministic_run_id for row in raw_market_snapshots)
    assert not any(row.get("run_id") == deterministic_run_id for row in raw_research_findings)


def test_run_once_uses_live_providers_in_paper_live_mode(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_runtime_mode(app_cfg, runtime_mode="PAPER_LIVE")

    live_market_payload = [
        {
            "id": "runtime-live-1",
            "question": "Will runtime mode selection test pass?",
            "active": True,
            "closed": False,
            "resolved": False,
            "updatedAt": "2026-03-14T11:55:00Z",
            "endDate": "2027-03-20T11:55:00Z",
            "outcomePrices": "[\"0.58\", \"0.42\"]",
            "liquidity": "45000",
            "volume24hr": "20000",
            "bestBid": "0.57",
            "bestAsk": "0.59",
            "priceChange24h": "0.004",
            "category": "testing",
        }
    ]
    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 500,
                    "title": "Prediction market testing",
                    "snippet": "Structured evidence for integration test.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W500",
                "display_name": "Runtime mode integration evidence",
                "publication_date": "2026-02-15",
                "primary_location": {
                    "landing_page_url": "https://example.org/research/runtime-500",
                    "source": {"display_name": "Runtime Journal"},
                },
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        if "example.test/markets" in url:
            return _MockHttpResponse(json.dumps(live_market_payload))
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    exit_code = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert exit_code == 0

    artifacts_dir = app_cfg.parent / "artifacts"
    raw_market_snapshots = _read_jsonl(artifacts_dir / "raw_market_snapshots.jsonl")
    raw_research_findings = _read_jsonl(artifacts_dir / "raw_research_findings.jsonl")
    assert raw_market_snapshots
    assert raw_research_findings


def test_run_once_fails_explicitly_on_live_provider_error_without_fallback(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_runtime_mode(app_cfg, runtime_mode="PAPER_LIVE", failure_policy="FAIL_FAST")

    def failing_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        raise error.URLError("simulated_network_failure")

    monkeypatch.setattr(http_client_module.request, "urlopen", failing_urlopen)

    exit_code = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert exit_code == 1

    events = _read_jsonl(app_cfg.parent / "audit" / "events.jsonl")
    assert any(row.get("event_type") == "live_market_fetch_failed" for row in events)
