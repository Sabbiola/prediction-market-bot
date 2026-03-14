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


class _MockHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_MockHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


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


def _configure_app_for_sandbox_chain(app_cfg: Path, *, rpc_url: str) -> None:
    payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    payload.setdefault("runtime", {})
    payload["runtime"]["mode"] = "SANDBOX_CHAIN"
    payload["runtime"]["market_data_provider"] = "STATIC"
    payload["runtime"]["research_provider"] = "STATIC"

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
    live_market_payload = [
        {
            "id": "acceptance-live-1",
            "question": "Will the beta acceptance gate pass?",
            "active": True,
            "closed": False,
            "resolved": False,
            "updatedAt": "2026-03-14T11:55:00Z",
            "endDate": "2026-03-20T11:55:00Z",
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
        del timeout
        url = getattr(req, "full_url", "")
        if "example.test/markets" in url:
            return _MockHttpResponse(json.dumps(live_market_payload))
        if "wikipedia.test" in url:
            return _MockHttpResponse(json.dumps(wikipedia_payload))
        if "openalex.test" in url:
            return _MockHttpResponse(json.dumps(openalex_payload))
        raise AssertionError(f"Unexpected URL in acceptance test: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)


def _report_value(report: str, key: str) -> str:
    marker = f"- {key}:"
    for line in report.splitlines():
        text = line.strip()
        if text.startswith(marker):
            return text.split(":", 1)[1].strip()
    return ""


def _must(condition: bool, message: str) -> None:
    assert condition, f"[beta-gate] {message}"


def _run_cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    exit_code = main(args)
    output = capsys.readouterr().out
    return exit_code, output


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

    run_exit, _ = _run_cli(
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
    _must(run_exit == 0, "run-once failed in PAPER_LIVE mode; operator cannot validate beta gate.")

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
    _must(show_exit == 0, "review-show failed; operator cannot inspect pending trade.")
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

    first_exit, _ = _run_cli(
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
    _must(first_exit == 0, "initial run failed before manual approval workflow.")

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
    _must(approve_exit == 0, "review-approve failed; operator cannot unlock execution.")
    approved_item = json.loads(approve_out)
    _must(approved_item["status"] == "APPROVED", "candidate did not transition to APPROVED state.")
    _must(
        approved_item["operator_rationale"] == "Approved after beta acceptance manual review.",
        "operator rationale was not persisted in review decision output.",
    )

    second_exit, _ = _run_cli(
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
    _must(second_exit == 0, "second run failed after approval; gate-to-execution handoff is broken.")

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
    _must(portfolio_exit == 0, "paper-portfolio-state command failed.")
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

    run_exit, _ = _run_cli(
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
    _must(run_exit == 0, "run-once failed before settlement-lane acceptance checks.")

    open_report_path = tmp_path / "open.md"
    open_report_exit, _ = _run_cli(
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
    _must(open_report_exit == 0, "generate-report failed before settlement lane.")
    open_report = open_report_path.read_text(encoding="utf-8")
    _must("settlement_queue" in open_report, "report is missing settlement queue section.")
    _must(_report_value(open_report, "pending") != "", "report is missing pending settlement count.")
    _must(_report_value(open_report, "open_positions") != "", "report is missing open_positions count.")

    settle_exit, _ = _run_cli(
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
    _must(settle_exit == 0, "settlement lane failed; open positions cannot be closed.")

    replay_exit, _ = _run_cli(
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
    _must(replay_exit == 0, "replay-run failed from persisted artifacts.")

    settled_report_path = tmp_path / "settled.md"
    settled_report_exit, _ = _run_cli(
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
    _must(settled_report_exit == 0, "generate-report failed after settlement lane.")
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

        first_exit, _ = _run_cli(
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
        _must(first_exit == 0, "initial SANDBOX_CHAIN run failed before review approval.")

        artifacts_dir = app_cfg.parent / "artifacts"
        queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
        queue_ids = [
            row["payload"]["queue_id"]  # type: ignore[index]
            for row in queue_rows
            if row.get("run_id") == run_id and isinstance(row.get("payload"), dict)
        ]
        _must(queue_ids, "no sandbox review candidate queued; cannot validate tx submission gate.")
        queue_id = str(queue_ids[0])

        approve_exit, _ = _run_cli(
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
        _must(approve_exit == 0, "sandbox review-approve failed.")

        second_exit, _ = _run_cli(
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
        _must(second_exit == 0, "SANDBOX_CHAIN run failed after approval.")

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
        _must(status_exit == 0, "tx-status command failed for sandbox acceptance.")
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
        _must(reconcile_exit == 0, "tx-reconcile failed for sandbox acceptance.")
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
