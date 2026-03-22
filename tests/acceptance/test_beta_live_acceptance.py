from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from prediction_market_bot.infrastructure import http_client as http_client_module
from prediction_market_bot.main import main
from prediction_market_bot.services import PaperPortfolioEngine
from prediction_market_bot.ui.app import create_web_app

pytestmark = pytest.mark.acceptance

_LAST_CLI_STDERR = ""


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


class _LocalSandboxRpcHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        method = payload.get("method")
        params = payload.get("params", [])

        result: object
        if method == "eth_call":
            result = "0x1"
        elif method == "eth_getTransactionCount":
            result = "0x2"
        elif method == "eth_gasPrice":
            result = "0x3b9aca00"
        elif method == "eth_estimateGas":
            result = "0x5208"
        elif method == "eth_sendTransaction":
            result = "0xlocaltx123"
        elif method == "eth_getTransactionByHash":
            tx_hash = params[0] if params else "0xlocaltx123"
            result = {"hash": tx_hash, "blockNumber": "0x10"}
        elif method == "eth_getTransactionReceipt":
            tx_hash = params[0] if params else "0xlocaltx123"
            result = {"transactionHash": tx_hash, "status": "0x1", "blockNumber": "0x10"}
        elif method == "eth_blockNumber":
            result = "0x10"
        else:
            result = None

        body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id", 1), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        del fmt, args


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _configure_pipeline_to_force_trade(agents_cfg: Path) -> None:
    payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    payload.setdefault("thresholds", {})
    payload["thresholds"]["min_confidence"] = 0.0
    payload["thresholds"]["min_edge_bps"] = -10000
    payload.setdefault("agents", {})
    prediction_cfg = payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    prediction_components = prediction_cfg.setdefault("components", {})  # type: ignore[assignment]
    prediction_components["market_weight"] = 0.0
    prediction_components["narrative_weight"] = 0.8
    prediction_components["structure_weight"] = 0.2
    payload.setdefault("risk", {})
    payload["risk"]["min_bet_usd"] = 1.0
    payload["risk"]["max_snapshot_age_sec"] = 999_999
    _write_yaml(agents_cfg, payload)


def _write_minimal_runtime_model_artifact(path: Path, *, model_version: str) -> None:
    payload = {
        "artifact_type": "prediction_model_v2",
        "model_name": "sandbox_rehearsal_tree_baseline",
        "model_version": model_version,
        "feature_schema_version": "v1",
        "feature_columns": ["f_market_yes_price"],
        "required_features": ["f_market_yes_price"],
        "algorithm_payload": {
            "algorithm": "tree_baseline",
            "feature_name": "f_market_yes_price",
            "threshold": 0.5,
            "left_probability": 0.72,
            "right_probability": 0.62,
            "global_probability": 0.62,
        },
        "calibration": {
            "artifact_type": "prediction_calibration_v2",
            "calibration_version": "cal-v1",
            "method": "platt",
            "parameters": {"a": 1.0, "b": 0.0},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_minimal_alt_runtime_model_artifact(path: Path, *, model_version: str) -> None:
    payload = {
        "artifact_type": "prediction_model_v2",
        "model_name": "sandbox_rehearsal_alt_llm_baseline",
        "model_version": model_version,
        "feature_schema_version": "alt-v1",
        "feature_columns": [
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        "required_features": [
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        "algorithm_payload": {
            "algorithm": "tree_baseline",
            "feature_name": "f_market_yes_price",
            "threshold": 0.5,
            "left_probability": 0.70,
            "right_probability": 0.61,
            "global_probability": 0.61,
        },
        "calibration": {
            "artifact_type": "prediction_calibration_v2",
            "calibration_version": "cal-alt-v1",
            "method": "platt",
            "parameters": {"a": 1.0, "b": 0.0},
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_prediction_engine_model_v2(
    agents_cfg: Path,
    *,
    model_artifact_path: Path,
    fallback_to_heuristic: bool,
    alt_shadow_model_artifact_path: Path | None = None,
    alt_shadow_promoted_enabled: bool = False,
    alt_shadow_required_source_coverage: list[str] | None = None,
    alt_shadow_require_llm_enrichment: bool = True,
) -> None:
    payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    payload.setdefault("agents", {})
    prediction_cfg = payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    model_inference = prediction_cfg.setdefault("model_inference", {})  # type: ignore[assignment]
    model_inference["engine"] = "model_v2"
    model_inference["model_artifact_path"] = str(model_artifact_path)
    model_inference["calibration_artifact_path"] = ""
    model_inference["feature_schema_version"] = "v1"
    model_inference["strict_feature_parity"] = True
    model_inference["fallback_to_heuristic"] = fallback_to_heuristic
    if alt_shadow_model_artifact_path is not None or alt_shadow_promoted_enabled:
        model_inference["alt_shadow"] = {
            "enabled": True,
            "model_artifact_path": str(alt_shadow_model_artifact_path) if alt_shadow_model_artifact_path else "",
            "calibration_artifact_path": "",
            "feature_schema_version": "alt-v1",
            "strict_feature_parity": True,
            "promoted_enabled": alt_shadow_promoted_enabled,
            "promoted_runtime_modes": ["SANDBOX_CHAIN"],
            "required_source_coverage": alt_shadow_required_source_coverage if alt_shadow_required_source_coverage is not None else ["news", "reddit", "x"],
            "require_llm_enrichment": alt_shadow_require_llm_enrichment,
        }
    _write_yaml(agents_cfg, payload)


def _configure_app_for_paper_live(app_cfg: Path, *, provider_selection: str, review_auto_approve: bool = False) -> None:
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload.setdefault("runtime", {})
    payload["runtime"]["mode"] = "PAPER_LIVE"
    payload["runtime"]["market_data_provider"] = provider_selection
    payload["runtime"]["research_provider"] = provider_selection
    payload["runtime"]["provider_failure_policy"] = "FAIL_FAST"

    payload.setdefault("execution", {})
    payload["execution"]["blocking_trade_review"] = False
    payload["execution"]["review_auto_approve"] = review_auto_approve
    payload["execution"]["settlement_same_run"] = False
    payload["execution"]["enable_rehearsal_lane"] = False

    payload.setdefault("feature_flags", {})
    payload["feature_flags"]["enable_manual_review_queue"] = False
    payload["feature_flags"]["blocking_trade_review"] = False

    payload.setdefault("live_market_data", {})
    payload["live_market_data"]["endpoint_url"] = "https://example.test/markets"
    payload["live_market_data"]["max_staleness_sec"] = 999_999
    payload["live_market_data"]["limit"] = 10

    payload.setdefault("live_research", {})
    payload["live_research"]["limit_per_source"] = 5
    wikipedia = payload["live_research"].setdefault("wikipedia", {})  # type: ignore[index]
    openalex = payload["live_research"].setdefault("openalex", {})  # type: ignore[index]
    if isinstance(wikipedia, dict):
        wikipedia["endpoint_url"] = "https://wikipedia.test/api.php"
    if isinstance(openalex, dict):
        openalex["endpoint_url"] = "https://openalex.test/works"
    _write_yaml(app_cfg, payload)


def _configure_app_for_sandbox_chain(
    app_cfg: Path,
    *,
    rpc_url: str,
    provider_selection: str = "STATIC",
) -> None:
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload.setdefault("runtime", {})
    payload["runtime"]["mode"] = "SANDBOX_CHAIN"
    payload["runtime"]["market_data_provider"] = provider_selection
    payload["runtime"]["research_provider"] = provider_selection
    payload["runtime"]["provider_failure_policy"] = "FAIL_FAST"

    payload.setdefault("execution", {})
    payload["execution"]["mode"] = "SANDBOX_CHAIN"
    payload["execution"]["blocking_trade_review"] = False
    payload["execution"]["review_auto_approve"] = False
    payload["execution"]["settlement_same_run"] = False
    payload["execution"]["enable_rehearsal_lane"] = False

    payload.setdefault("feature_flags", {})
    payload["feature_flags"]["enable_manual_review_queue"] = False
    payload["feature_flags"]["blocking_trade_review"] = False

    payload.setdefault("sandbox_chain", {})
    payload["sandbox_chain"]["enabled"] = True
    payload["sandbox_chain"]["submit_tx"] = True
    payload["sandbox_chain"]["rpc_url"] = rpc_url
    payload["sandbox_chain"]["allow_unlocked_send"] = True
    payload["sandbox_chain"]["contract_address"] = "0x1234567890123456789012345678901234567890"
    payload["sandbox_chain"]["from_address"] = "0x1111111111111111111111111111111111111111"
    payload["sandbox_chain"]["private_key_env"] = "SANDBOX_CHAIN_PRIVATE_KEY"

    payload.setdefault("live_market_data", {})
    payload["live_market_data"]["endpoint_url"] = "https://example.test/markets"
    payload["live_market_data"]["max_staleness_sec"] = 999_999
    payload["live_market_data"]["limit"] = 10

    payload.setdefault("live_research", {})
    payload["live_research"]["limit_per_source"] = 5
    wikipedia = payload["live_research"].setdefault("wikipedia", {})  # type: ignore[index]
    openalex = payload["live_research"].setdefault("openalex", {})  # type: ignore[index]
    if isinstance(wikipedia, dict):
        wikipedia["endpoint_url"] = "https://wikipedia.test/api.php"
    if isinstance(openalex, dict):
        openalex["endpoint_url"] = "https://openalex.test/works"
    _write_yaml(app_cfg, payload)


def _configure_ui_auth(app_cfg: Path, *, timeout_sec: int = 1800) -> None:
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload["ui_auth"] = {
        "enabled": True,
        "session_secret_env": "PM_BOT_UI_SESSION_SECRET",
        "session_timeout_sec": timeout_sec,
        "cookie_name": "pm_bot_ui_session",
        "cookie_secure": False,
        "cookie_samesite": "lax",
        "users": [
            {"username": "viewer", "role": "viewer", "password": "viewer-pass"},
            {"username": "operator", "role": "operator", "password": "operator-pass"},
            {"username": "admin", "role": "admin", "password": "admin-pass"},
        ],
    }
    _write_yaml(app_cfg, payload)


def _patch_live_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    original_urlopen = http_client_module.request.urlopen
    live_market_payload = [
        {
            "id": "acceptance-live-1",
            "question": "Will the beta acceptance gate pass?",
            "active": True,
            "closed": False,
            "resolved": False,
            "updatedAt": "2026-03-14T11:55:00Z",
            "endDate": "2027-03-20T11:55:00Z",
            "outcomePrices": "[\"0.42\", \"0.58\"]",
            "liquidity": "45000",
            "volume24hr": "20000",
            "bestBid": "0.41",
            "bestAsk": "0.43",
            "priceChange24h": "0.004",
            "category": "testing",
        }
    ]
    wikipedia_payload = {
        "query": {
            "search": [
                {
                    "pageid": 500,
                    "title": "Prediction market acceptance",
                    "snippet": "Deterministic evidence for beta acceptance tests.",
                    "timestamp": "2026-03-13T10:00:00Z",
                }
            ]
        }
    }
    openalex_payload = {
        "results": [
            {
                "id": "https://openalex.org/W500",
                "display_name": "Acceptance integration evidence",
                "publication_date": "2026-02-15",
                "primary_location": {
                    "landing_page_url": "https://example.org/research/acceptance-500",
                    "source": {"display_name": "Acceptance Journal"},
                },
            }
        ]
    }

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        if "127.0.0.1" in getattr(req, "full_url", "") or "localhost" in getattr(req, "full_url", ""):
            return original_urlopen(req, timeout=timeout)
        url = getattr(req, "full_url", "")
        if "example.test/markets" in url:
            return _MockHttpResponse(json.dumps(live_market_payload))
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL in acceptance test: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)


def _patch_research_bundle_with_alt_features(
    monkeypatch: pytest.MonkeyPatch,
    *,
    feature_bundle: dict[str, float],
) -> None:
    from prediction_market_bot.agents import research as research_module

    class _FixedBundle:
        def __init__(self, payload: dict[str, float]) -> None:
            self._payload = dict(payload)

        def to_runtime_dict(self) -> dict[str, float]:
            return dict(self._payload)

    def _fake_bundle_from_findings(**_: object) -> _FixedBundle:
        return _FixedBundle(feature_bundle)

    monkeypatch.setattr(research_module, "build_bundle_from_findings", _fake_bundle_from_findings)


def _report_value(report: str, key: str) -> str:
    marker = f"- {key}:"
    for line in report.splitlines():
        text = line.strip()
        if text.startswith(marker):
            return text.split(":", 1)[1].strip()
    return ""


def _must(condition: bool, message: str) -> None:
    assert condition, f"[beta-gate] {message}"


def _must_ok(exit_code: int, output: str, message: str) -> None:
    if exit_code == 0:
        return
    details = output.strip() or _LAST_CLI_STDERR or "<no CLI output captured>"
    assert False, f"[beta-gate] {message} CLI output: {details}"


def _run_cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    global _LAST_CLI_STDERR
    exit_code = main(args)
    captured = capsys.readouterr()
    _LAST_CLI_STDERR = captured.err.strip()
    return exit_code, captured.out.strip()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _local_sandbox_rpc_server() -> Iterator[str]:
    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), _LocalSandboxRpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_beta_acceptance_paper_live_blocks_until_manual_review(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_app_for_paper_live(app_cfg, provider_selection="AUTO")
    _configure_pipeline_to_force_trade(agents_cfg)
    _patch_live_providers(monkeypatch)

    run_exit, run_out = _run_cli(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(run_exit, run_out, "run-once failed in PAPER_LIVE mode; operator cannot validate beta gate.")

    artifacts_dir = app_cfg.parent / "artifacts"
    raw_market = _read_jsonl(artifacts_dir / "raw_market_snapshots.jsonl")
    raw_research = _read_jsonl(artifacts_dir / "raw_research_findings.jsonl")
    queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
    gate_rows = _read_jsonl(artifacts_dir / "trade_review_gate_decisions.jsonl")
    execution_rows = _read_jsonl(artifacts_dir / "execution_results.jsonl")

    _must(
        bool(raw_market),
        "no raw live market snapshot persisted; check PAPER_LIVE provider wiring.",
    )
    _must(
        bool(raw_research),
        "no raw live research finding persisted; check PAPER_LIVE research adapter wiring.",
    )

    queue_ids = [
        row["payload"]["queue_id"]  # type: ignore[index]
        for row in queue_rows
        if row.get("run_id") == deterministic_run_id and isinstance(row.get("payload"), dict)
    ]
    _must(queue_ids, "no review candidate queued; blocking human gate cannot be exercised.")
    _must(
        any(
            row.get("run_id") == deterministic_run_id
            and isinstance(row.get("payload"), dict)
            and not bool(row["payload"].get("allowed", True))  # type: ignore[index]
            for row in gate_rows
        ),
        "trade review gate did not block pending candidate; execution safety violated.",
    )
    _must(
        any(
            row.get("run_id") == deterministic_run_id
            and isinstance(row.get("payload"), dict)
            and row["payload"].get("status") == "SKIPPED"  # type: ignore[index]
            for row in execution_rows
        ),
        "execution was not skipped while pending review existed.",
    )

    show_exit, show_out = _run_cli(
        [
            "review-show",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            str(queue_ids[0]),
            "--json",
        ],
        capsys,
    )
    _must_ok(show_exit, show_out, "review-show failed; operator cannot inspect pending trade.")
    shown = json.loads(show_out)
    _must(shown["status"] == "PENDING_REVIEW", "review item is not in PENDING_REVIEW state.")
    _must(bool(shown["model_rationale"]), "model rationale is missing from queued candidate.")


def test_beta_acceptance_approval_unblocks_execution_and_persists_open_position(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_app_for_paper_live(app_cfg, provider_selection="AUTO")
    _configure_pipeline_to_force_trade(agents_cfg)
    _patch_live_providers(monkeypatch)

    first_exit, first_out = _run_cli(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(first_exit, first_out, "initial run failed before manual approval workflow.")

    artifacts_dir = app_cfg.parent / "artifacts"
    queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
    queue_ids = [
        row["payload"]["queue_id"]  # type: ignore[index]
        for row in queue_rows
        if row.get("run_id") == deterministic_run_id and isinstance(row.get("payload"), dict)
    ]
    _must(queue_ids, "no queued item found; cannot proceed with approval flow.")
    queue_id = str(queue_ids[0])

    approve_exit, approve_out = _run_cli(
        [
            "review-approve",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            queue_id,
            "--operator-id",
            "beta-operator",
            "--rationale",
            "Approved after beta acceptance manual review.",
        ],
        capsys,
    )
    _must_ok(approve_exit, approve_out, "review-approve failed; operator cannot unlock execution.")
    approved_item = json.loads(approve_out)
    _must(approved_item["status"] == "APPROVED", "candidate did not transition to APPROVED state.")
    _must(
        approved_item["operator_rationale"] == "Approved after beta acceptance manual review.",
        "operator rationale was not persisted in review decision output.",
    )

    second_exit, second_out = _run_cli(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(second_exit, second_out, "second run failed after approval; gate-to-execution handoff is broken.")

    gate_rows_after = _read_jsonl(artifacts_dir / "trade_review_gate_decisions.jsonl")
    execution_rows_after = _read_jsonl(artifacts_dir / "execution_results.jsonl")
    decisions_rows = _read_jsonl(artifacts_dir / "trade_review_decisions.jsonl")
    _must(
        any(
            row.get("run_id") == deterministic_run_id
            and isinstance(row.get("payload"), dict)
            and row["payload"].get("queue_id") == queue_id  # type: ignore[index]
            and bool(row["payload"].get("allowed", False))  # type: ignore[index]
            for row in gate_rows_after
        ),
        "trade remained blocked after approval; review gate decision did not flip to allowed.",
    )
    _must(
        any(
            row.get("run_id") == deterministic_run_id
            and isinstance(row.get("payload"), dict)
            and row["payload"].get("status") == "FILLED"  # type: ignore[index]
            for row in execution_rows_after
        ),
        "no FILLED execution found after approval.",
    )
    _must(
        any(
            row.get("run_id") == deterministic_run_id
            and isinstance(row.get("payload"), dict)
            and row["payload"].get("operator_rationale") == "Approved after beta acceptance manual review."  # type: ignore[index]
            for row in decisions_rows
        ),
        "trade review decision artifact is missing operator rationale.",
    )

    portfolio_exit, portfolio_out = _run_cli(
        [
            "paper-portfolio-state",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
        ],
        capsys,
    )
    _must_ok(portfolio_exit, portfolio_out, "paper-portfolio-state command failed.")
    portfolio_snapshot = json.loads(portfolio_out)
    _must(
        int(portfolio_snapshot.get("open_position_count", 0)) >= 1,
        "open positions were not persisted after approved execution.",
    )

    rows = _read_jsonl(artifacts_dir / "paper_portfolio_events.jsonl")
    engine_a = PaperPortfolioEngine()
    engine_a.replay_rows(rows)
    snapshot_a = engine_a.snapshot()
    engine_b = PaperPortfolioEngine()
    engine_b.replay_rows(rows)
    snapshot_b = engine_b.snapshot()
    _must(
        snapshot_b.open_position_count == snapshot_a.open_position_count and snapshot_b.open_position_count >= 1,
        "open position replay is not stable across restart simulation.",
    )


def test_beta_acceptance_async_settlement_lane_and_reporting(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_app_for_paper_live(app_cfg, provider_selection="STATIC", review_auto_approve=True)
    _configure_pipeline_to_force_trade(agents_cfg)

    run_exit, run_out = _run_cli(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(run_exit, run_out, "run-once failed before settlement-lane acceptance checks.")

    open_report_path = tmp_path / "open.md"
    open_report_exit, open_report_out = _run_cli(
        [
            "generate-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(open_report_path),
        ],
        capsys,
    )
    _must_ok(open_report_exit, open_report_out, "generate-report failed before settlement lane.")
    open_report = open_report_path.read_text(encoding="utf-8")
    _must("settlement_queue" in open_report, "report is missing settlement queue section.")
    _must(_report_value(open_report, "pending") != "", "report is missing pending settlement count.")
    _must(_report_value(open_report, "open_positions") != "", "report is missing open_positions count.")

    settle_exit, settle_out = _run_cli(
        [
            "run-settlement-lane",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(settle_exit, settle_out, "settlement lane failed; open positions cannot be closed.")

    replay_exit, replay_out = _run_cli(
        [
            "replay-run",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ],
        capsys,
    )
    _must_ok(replay_exit, replay_out, "replay-run failed from persisted artifacts.")

    settled_report_path = tmp_path / "settled.md"
    settled_report_exit, settled_report_out = _run_cli(
        [
            "generate-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--output",
            str(settled_report_path),
        ],
        capsys,
    )
    _must_ok(settled_report_exit, settled_report_out, "generate-report failed after settlement lane.")
    settled_report = settled_report_path.read_text(encoding="utf-8")
    _must(_report_value(settled_report, "pending") == "0", "pending settlement count should be 0 after settlement lane.")
    _must(_report_value(settled_report, "open_positions") == "0", "open_positions should be 0 after settlement lane.")


def test_beta_acceptance_sandbox_chain_submission_path(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_pipeline_to_force_trade(agents_cfg)

    with _local_sandbox_rpc_server() as rpc_url:
        _configure_app_for_sandbox_chain(app_cfg, rpc_url=rpc_url)
        run_id = f"{deterministic_run_id}-sandbox"

        first_exit, first_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(first_exit, first_out, "initial SANDBOX_CHAIN run failed before review approval.")

        artifacts_dir = app_cfg.parent / "artifacts"
        queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
        queue_ids = [
            row["payload"]["queue_id"]  # type: ignore[index]
            for row in queue_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(queue_ids, "no sandbox review candidate queued; cannot validate tx submission gate.")
        queue_id = str(queue_ids[0])

        approve_exit, approve_out = _run_cli(
            [
                "review-approve",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--queue-id",
                queue_id,
                "--operator-id",
                "sandbox-operator",
                "--rationale",
                "Approved sandbox rehearsal transaction.",
            ],
            capsys,
        )
        _must_ok(approve_exit, approve_out, "sandbox review-approve failed.")

        second_exit, second_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(second_exit, second_out, "SANDBOX_CHAIN run failed after approval.")

        intents = _read_jsonl(artifacts_dir / "order_intents.jsonl")
        attempts = _read_jsonl(artifacts_dir / "transaction_attempts.jsonl")
        execution_rows = _read_jsonl(artifacts_dir / "execution_results.jsonl")

        _must(
            any(
                row.get("run_id") == run_id
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("review_queue_id") == queue_id  # type: ignore[index]
                for row in intents
            ),
            "order_intent artifact missing review_queue_id traceability.",
        )
        _must(
            any(
                row.get("run_id") == run_id
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("tx_hash") == "0xlocaltx123"  # type: ignore[index]
                for row in attempts
            ),
            "sandbox transaction attempt missing expected tx_hash.",
        )
        _must(
            any(
                row.get("run_id") == run_id
                and isinstance(row.get("payload"), dict)
                and row["payload"].get("status") == "SUBMITTED"  # type: ignore[index]
                for row in execution_rows
            ),
            "execution result is not SUBMITTED in SANDBOX_CHAIN mode.",
        )

        status_exit, status_out = _run_cli(
            [
                "tx-status",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--json",
            ],
            capsys,
        )
        _must_ok(status_exit, status_out, "tx-status command failed for sandbox acceptance.")
        status_payload = json.loads(status_out)
        _must(bool(status_payload), "tx-status returned empty payload for approved sandbox run.")
        _must(
            any(item.get("latest_tx_hash") == "0xlocaltx123" for item in status_payload),
            "tx-status does not expose expected latest_tx_hash.",
        )

        reconcile_exit, reconcile_out = _run_cli(
            [
                "tx-reconcile",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--json",
            ],
            capsys,
        )
        _must_ok(reconcile_exit, reconcile_out, "tx-reconcile failed for sandbox acceptance.")
        reconcile_payload = json.loads(reconcile_out)
        _must(bool(reconcile_payload), "tx-reconcile returned empty payload.")
        _must(
            any(item.get("confirmation_status") == "MINED" for item in reconcile_payload),
            "reconciled transaction is not MINED on local sandbox chain.",
        )


def test_beta_acceptance_ui_control_plane_path(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_pipeline_to_force_trade(agents_cfg)
    _configure_ui_auth(app_cfg)
    monkeypatch.setenv("PM_BOT_UI_SESSION_SECRET", "beta-ui-session-secret-123456")

    with _local_sandbox_rpc_server() as rpc_url:
        _configure_app_for_sandbox_chain(app_cfg, rpc_url=rpc_url)
        run_id = f"{deterministic_run_id}-ui-beta"

        app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "operator", "password": "operator-pass"})
            _must(login.status_code == 200, "ui login endpoint failed.")
            login_payload = login.json()
            _must(bool(login_payload.get("authenticated")), "ui login did not authenticate operator role.")

            run_first = client.post("/api/actions/run-once", json={"run_id": run_id, "force": False})
            _must(run_first.status_code == 200, "ui run-once failed to enqueue review candidate.")
            _must(run_first.json().get("status") == "completed", "ui run-once did not complete initial run.")

            overview = client.get(f"/api/tabs/overview?run_id={run_id}")
            _must(overview.status_code == 200, "ui overview tab is not readable after login.")
            _must(
                overview.json().get("run_selector", {}).get("selected_run_id") == run_id,
                "ui overview did not select the expected run_id.",
            )

            review_tab = client.get(f"/api/tabs/review-queue?run_id={run_id}&status=PENDING_REVIEW")
            _must(review_tab.status_code == 200, "ui review queue tab failed.")
            review_rows = review_tab.json().get("rows") or []
            _must(bool(review_rows), "ui review queue has no pending candidate to approve.")
            queue_id = str(review_rows[0]["queue_id"])

            approve = client.post(
                "/api/actions/review-approve",
                json={
                    "queue_id": queue_id,
                    "operator_id": "ui-beta-operator",
                    "rationale": "Approved from UI acceptance flow.",
                    "note": "beta gate",
                    "confirm": True,
                },
            )
            _must(approve.status_code == 200, "ui review-approve endpoint failed.")
            _must(
                approve.json().get("status") == "completed",
                "ui review-approve did not persist APPROVED decision.",
            )

            run_second = client.post("/api/actions/run-once", json={"run_id": run_id, "force": False})
            _must(run_second.status_code == 200, "ui run-once failed after approval.")
            _must(
                run_second.json().get("status") == "completed",
                "ui second run did not complete after review approval.",
            )

            sandbox_tab = client.get(f"/api/tabs/sandbox-tx?run_id={run_id}")
            _must(sandbox_tab.status_code == 200, "ui sandbox tx tab failed.")
            sandbox_payload = sandbox_tab.json()
            _must(
                int(sandbox_payload.get("attempts_count", 0)) >= 1,
                "ui sandbox tx tab has no attempts after approved run.",
            )

            reconcile = client.post("/api/actions/tx-reconcile", json={"run_id": run_id, "intent_id": "", "limit": 50})
            _must(reconcile.status_code == 200, "ui tx-reconcile endpoint failed.")
            _must(
                reconcile.json().get("status") == "completed",
                "ui tx-reconcile did not complete successfully.",
            )


def test_beta_acceptance_sandbox_live_promoted_model_rehearsal(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_pipeline_to_force_trade(agents_cfg)
    _patch_live_providers(monkeypatch)

    model_version = "v2-sandbox-rehearsal"
    model_artifact = tmp_path / "sandbox_model_v2.json"
    _write_minimal_runtime_model_artifact(model_artifact, model_version=model_version)
    _configure_prediction_engine_model_v2(
        agents_cfg,
        model_artifact_path=model_artifact,
        fallback_to_heuristic=False,
    )

    with _local_sandbox_rpc_server() as rpc_url:
        _configure_app_for_sandbox_chain(app_cfg, rpc_url=rpc_url, provider_selection="AUTO")
        run_id = f"{deterministic_run_id}-sandbox-v2"

        promote_exit, promote_out = _run_cli(
            [
                "promote-model-v2",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--rationale",
                "beta gate sandbox-live v2 rehearsal",
                "--model-version",
                model_version,
                "--force",
                "--json",
            ],
            capsys,
        )
        _must_ok(promote_exit, promote_out, "promote-model-v2 failed; cannot activate model_v2 in sandbox-live.")
        promote_payload = json.loads(promote_out)
        _must(promote_payload.get("status") == "promoted", "model promotion command did not return promoted status.")

        status_exit, status_out = _run_cli(
            [
                "model-promotion-status",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--json",
            ],
            capsys,
        )
        _must_ok(status_exit, status_out, "model-promotion-status failed before sandbox-live rehearsal run.")
        status_payload = json.loads(status_out)
        gate_decision = status_payload.get("gate_decision", {})
        _must(
            gate_decision.get("reason") == "model_v2_allowed",
            "model promotion gate is not satisfied in SANDBOX_CHAIN mode.",
        )
        _must(
            gate_decision.get("effective_engine") == "model_v2",
            "runtime gate did not activate model_v2 in SANDBOX_CHAIN mode.",
        )

        startup_exit, startup_out = _run_cli(
            [
                "validate-startup",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
            ],
            capsys,
        )
        _must_ok(startup_exit, startup_out, "startup validation failed before sandbox-live rehearsal.")

        first_exit, first_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(first_exit, first_out, "initial sandbox-live run failed before review approval.")

        artifacts_dir = app_cfg.parent / "artifacts"
        raw_market = _read_jsonl(artifacts_dir / "raw_market_snapshots.jsonl")
        raw_research = _read_jsonl(artifacts_dir / "raw_research_findings.jsonl")
        queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
        _must(bool(raw_market), "sandbox-live rehearsal did not persist live market source payloads.")
        _must(bool(raw_research), "sandbox-live rehearsal did not persist live research source payloads.")
        queue_ids = [
            row["payload"]["queue_id"]  # type: ignore[index]
            for row in queue_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(queue_ids, "review queue did not capture pending candidate in sandbox-live rehearsal.")
        queue_id = str(queue_ids[0])

        approve_exit, approve_out = _run_cli(
            [
                "review-approve",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--queue-id",
                queue_id,
                "--operator-id",
                "sandbox-v2-operator",
                "--rationale",
                "Approved in sandbox-live v2 rehearsal.",
            ],
            capsys,
        )
        _must_ok(approve_exit, approve_out, "review-approve failed in sandbox-live rehearsal.")

        second_exit, second_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(second_exit, second_out, "sandbox-live run failed after approval.")

        tx_status_exit, tx_status_out = _run_cli(
            [
                "tx-status",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--json",
            ],
            capsys,
        )
        _must_ok(tx_status_exit, tx_status_out, "tx-status failed after sandbox submission.")
        tx_status_payload = json.loads(tx_status_out)
        _must(
            any(item.get("latest_tx_hash") == "0xlocaltx123" for item in tx_status_payload),
            "sandbox tx submission hash is missing from tx-status output.",
        )

        reconcile_exit, reconcile_out = _run_cli(
            [
                "tx-reconcile",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--json",
            ],
            capsys,
        )
        _must_ok(reconcile_exit, reconcile_out, "tx-reconcile failed in sandbox-live rehearsal.")
        reconcile_payload = json.loads(reconcile_out)
        _must(
            any(item.get("confirmation_status") == "MINED" for item in reconcile_payload),
            "sandbox tx did not reach MINED during rehearsal reconcile step.",
        )

        portfolio_exit, portfolio_out = _run_cli(
            [
                "paper-portfolio-state",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
            ],
            capsys,
        )
        _must_ok(portfolio_exit, portfolio_out, "paper-portfolio-state failed in sandbox-live rehearsal.")
        portfolio_payload = json.loads(portfolio_out)
        _must(
            "open_position_count" in portfolio_payload,
            "portfolio state does not expose open position count in sandbox-live rehearsal.",
        )

        settlement_exit, settlement_out = _run_cli(
            [
                "run-settlement-lane",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(settlement_exit, settlement_out, "settlement lane command failed in sandbox-live rehearsal.")

        replay_exit, replay_out = _run_cli(
            [
                "replay-run",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(replay_exit, replay_out, "replay-run failed in sandbox-live rehearsal.")

        report_path = tmp_path / "sandbox_v2_report.md"
        report_exit, report_out = _run_cli(
            [
                "generate-report",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
                "--output",
                str(report_path),
            ],
            capsys,
        )
        _must_ok(report_exit, report_out, "generate-report failed in sandbox-live rehearsal.")
        report = report_path.read_text(encoding="utf-8")
        _must("settlement_queue" in report, "report is missing settlement queue section in sandbox-live rehearsal.")
        _must(_report_value(report, "open_positions") != "", "report is missing open_positions field.")

        app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
        with TestClient(app) as client:
            overview = client.get(f"/api/tabs/overview?run_id={run_id}")
            _must(overview.status_code == 200, "overview tab failed in sandbox-live v2 rehearsal.")
            overview_payload = overview.json()
            model_visibility = overview_payload.get("model_visibility", {})
            _must(
                model_visibility.get("active_model_version") == model_version,
                "overview tab does not show active promoted model version.",
            )
            _must(
                model_visibility.get("effective_engine") == "model_v2",
                "overview tab does not show model_v2 as effective engine.",
            )

            prediction = client.get(f"/api/tabs/prediction?run_id={run_id}")
            _must(prediction.status_code == 200, "prediction tab failed in sandbox-live v2 rehearsal.")
            prediction_payload = prediction.json()
            prediction_visibility = prediction_payload.get("model_visibility", {})
            _must(
                prediction_visibility.get("active_model_version") == model_version,
                "prediction tab does not expose active model version for operator visibility.",
            )
            _must(
                "approval_rate_summary" in prediction_payload,
                "prediction tab is missing approval-rate summary payload.",
            )
            _must(
                "disagreement_buckets" in prediction_payload,
                "prediction tab is missing disagreement buckets payload.",
            )


def test_beta_acceptance_sandbox_live_alt_promoted_path_is_visible_in_ui(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_pipeline_to_force_trade(agents_cfg)
    _patch_live_providers(monkeypatch)
    _patch_research_bundle_with_alt_features(
        monkeypatch,
        feature_bundle={
            "f_alt_news_volume_24h": 6.0,
            "f_alt_reddit_mentions_24h": 3.0,
            "f_alt_x_mentions_24h": 4.0,
            "f_alt_enrichment_coverage": 0.8,
        },
    )

    model_version = "v2-sandbox-alt-promoted"
    model_artifact = tmp_path / "sandbox_model_v2_alt_base.json"
    _write_minimal_runtime_model_artifact(model_artifact, model_version=model_version)
    alt_model_artifact = tmp_path / "sandbox_model_v2_alt_enriched.json"
    _write_minimal_alt_runtime_model_artifact(alt_model_artifact, model_version=f"{model_version}-alt")
    _configure_prediction_engine_model_v2(
        agents_cfg,
        model_artifact_path=model_artifact,
        fallback_to_heuristic=False,
        alt_shadow_model_artifact_path=alt_model_artifact,
        alt_shadow_promoted_enabled=True,
        alt_shadow_required_source_coverage=["news", "reddit", "x"],
        alt_shadow_require_llm_enrichment=True,
    )

    with _local_sandbox_rpc_server() as rpc_url:
        _configure_app_for_sandbox_chain(app_cfg, rpc_url=rpc_url, provider_selection="AUTO")
        run_id = f"{deterministic_run_id}-sandbox-alt-promoted"

        promote_exit, promote_out = _run_cli(
            [
                "promote-model-v2",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--rationale",
                "beta gate sandbox-live alt promoted rehearsal",
                "--model-version",
                model_version,
                "--force",
                "--json",
            ],
            capsys,
        )
        _must_ok(promote_exit, promote_out, "promote-model-v2 failed before alt promoted rehearsal.")

        first_exit, first_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(first_exit, first_out, "first sandbox run failed before review approval in alt promoted rehearsal.")

        artifacts_dir = app_cfg.parent / "artifacts"
        queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
        queue_ids = [
            row["payload"]["queue_id"]  # type: ignore[index]
            for row in queue_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(queue_ids, "no review candidate queued in alt promoted rehearsal.")
        queue_id = str(queue_ids[0])

        approve_exit, approve_out = _run_cli(
            [
                "review-approve",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--queue-id",
                queue_id,
                "--operator-id",
                "sandbox-alt-operator",
                "--rationale",
                "Approved in sandbox-live alt promoted rehearsal.",
            ],
            capsys,
        )
        _must_ok(approve_exit, approve_out, "review-approve failed in alt promoted rehearsal.")

        second_exit, second_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(second_exit, second_out, "second sandbox run failed in alt promoted rehearsal.")

        alt_rows = _read_jsonl(artifacts_dir / "prediction_alt_comparisons.jsonl")
        run_rows = [
            row
            for row in alt_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(run_rows, "alt promoted comparison artifact is missing for promoted sandbox run.")
        first_payload = run_rows[0]["payload"]  # type: ignore[index]
        _must(
            first_payload.get("primary_prediction") == "alt_llm_promoted",  # type: ignore[union-attr]
            "alt promoted path did not become primary after promotion in sandbox-live.",
        )

        app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)
        with TestClient(app) as client:
            prediction = client.get(f"/api/tabs/prediction?run_id={run_id}")
            _must(prediction.status_code == 200, "prediction tab failed in alt promoted rehearsal.")
            payload = prediction.json()
            visibility = payload.get("model_visibility", {})
            _must(
                visibility.get("active_source_set") == ["news", "reddit", "x"],
                "UI does not expose active source set for alt promoted path.",
            )
            _must(
                payload.get("enrichment_coverage") is not None,
                "UI does not expose enrichment coverage for alt promoted path.",
            )
            _must(
                isinstance(payload.get("disagreement_vs_baseline"), list),
                "UI does not expose disagreement-vs-baseline payload for alt promoted path.",
            )
            _must(
                payload.get("drift_alert") is not None,
                "UI does not expose drift/degradation signals in alt promoted path.",
            )


def test_beta_acceptance_alt_source_capability_failure_is_explicit(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload["alt_data"] = {
        "enabled": True,
        "sources": {
            "reddit": {
                "enabled": True,
                "source_class": "reddit",
                "adapter": "reddit_oauth_adapter",
                "credential_env": "REDDIT_ACCESS_TOKEN",
                "capabilities": {
                    "requires_oauth": True,
                    "requires_user_context": False,
                    "supports_backfill": True,
                    "supports_live_polling": True,
                    "supports_search": True,
                    "supports_thread_context_expansion": True,
                },
            }
        },
    }
    _write_yaml(app_cfg, payload)

    validate_exit, validate_out = _run_cli(
        [
            "validate-startup",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ],
        capsys,
    )
    _must(validate_exit == 1, "startup validation did not fail on missing reddit oauth credential.")
    output = validate_out.strip() or _LAST_CLI_STDERR
    parsed = json.loads(output)
    checks = {row["name"]: row for row in parsed.get("checks", [])}
    _must("alt_data_sources" in checks, "startup report missing alt_data_sources check.")
    _must(checks["alt_data_sources"]["ok"] is False, "alt_data_sources check must fail when oauth credential is missing.")


def test_beta_acceptance_alt_promoted_path_falls_back_gracefully_when_coverage_missing(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _configure_pipeline_to_force_trade(agents_cfg)
    _patch_live_providers(monkeypatch)

    model_version = "v2-sandbox-alt-fallback"
    model_artifact = tmp_path / "sandbox_model_v2_fallback_base.json"
    _write_minimal_runtime_model_artifact(model_artifact, model_version=model_version)
    alt_model_artifact = tmp_path / "sandbox_model_v2_fallback_alt.json"
    _write_minimal_alt_runtime_model_artifact(alt_model_artifact, model_version=f"{model_version}-alt")
    _configure_prediction_engine_model_v2(
        agents_cfg,
        model_artifact_path=model_artifact,
        fallback_to_heuristic=False,
        alt_shadow_model_artifact_path=alt_model_artifact,
        alt_shadow_promoted_enabled=True,
        alt_shadow_required_source_coverage=["news", "reddit", "x"],
        alt_shadow_require_llm_enrichment=True,
    )

    with _local_sandbox_rpc_server() as rpc_url:
        _configure_app_for_sandbox_chain(app_cfg, rpc_url=rpc_url, provider_selection="AUTO")
        run_id = f"{deterministic_run_id}-sandbox-alt-fallback"

        promote_exit, promote_out = _run_cli(
            [
                "promote-model-v2",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--rationale",
                "beta gate sandbox-live alt fallback rehearsal",
                "--model-version",
                model_version,
                "--force",
                "--json",
            ],
            capsys,
        )
        _must_ok(promote_exit, promote_out, "promote-model-v2 failed before alt fallback rehearsal.")

        first_exit, first_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(first_exit, first_out, "first sandbox run failed before approval in alt fallback rehearsal.")

        artifacts_dir = app_cfg.parent / "artifacts"
        queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
        queue_ids = [
            row["payload"]["queue_id"]  # type: ignore[index]
            for row in queue_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(queue_ids, "no review candidate queued in alt fallback rehearsal.")

        approve_exit, approve_out = _run_cli(
            [
                "review-approve",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--queue-id",
                str(queue_ids[0]),
                "--operator-id",
                "sandbox-alt-fallback-operator",
                "--rationale",
                "Approved in sandbox-live alt fallback rehearsal.",
            ],
            capsys,
        )
        _must_ok(approve_exit, approve_out, "review-approve failed in alt fallback rehearsal.")

        second_exit, second_out = _run_cli(
            [
                "run-once",
                "--config",
                str(app_cfg),
                "--agents-config",
                str(agents_cfg),
                "--run-id",
                run_id,
            ],
            capsys,
        )
        _must_ok(second_exit, second_out, "second sandbox run failed in alt fallback rehearsal.")

        prediction_rows = _read_jsonl(artifacts_dir / "prediction_results.jsonl")
        matching_predictions = [
            row
            for row in prediction_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(matching_predictions, "prediction results missing in alt fallback rehearsal.")
        rationales = matching_predictions[0]["payload"].get("rationale", [])  # type: ignore[index]
        _must(
            isinstance(rationales, list) and any("alt_llm_promoted_fallback=model_v2" in str(item) for item in rationales),
            "graceful fallback to baseline model_v2 was not recorded in prediction rationale.",
        )

        alt_rows = _read_jsonl(artifacts_dir / "prediction_alt_comparisons.jsonl")
        matching_alt = [
            row
            for row in alt_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(matching_alt, "prediction_alt_comparisons artifact missing in alt fallback rehearsal.")
        alt_payload = matching_alt[0]["payload"]  # type: ignore[index]
        parity_warnings = alt_payload.get("parity_warnings", [])  # type: ignore[union-attr]
        _must(
            isinstance(parity_warnings, list) and any("missing_source_coverage" in str(item) for item in parity_warnings),
            "alt fallback path did not expose source coverage warnings.",
        )
