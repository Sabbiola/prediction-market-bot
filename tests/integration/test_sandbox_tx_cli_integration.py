from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.bootstrap import build_operational_repositories
from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import TxAttempt, TxIntent
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


def _intent_id(intent: TxIntent) -> str:
    payload = (
        f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
        f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _set_sandbox_chain_enabled(app_cfg: Path, *, submit_tx: bool) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    sandbox = raw.setdefault("sandbox_chain", {})
    if not isinstance(sandbox, dict):
        raise AssertionError("sandbox_chain section must be a mapping")
    sandbox["enabled"] = True
    sandbox["submit_tx"] = submit_tx
    sandbox["rpc_url"] = "https://sandbox-chain.example/rpc"
    sandbox["contract_address"] = "0x1234567890123456789012345678901234567890"
    sandbox["from_address"] = "0x1111111111111111111111111111111111111111"
    sandbox["allow_unlocked_send"] = True
    sandbox["private_key_env"] = "SANDBOX_CHAIN_PRIVATE_KEY"
    raw["sandbox_chain"] = sandbox
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_tx_status_and_reconcile_commands(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_sandbox_chain_enabled(app_cfg, submit_tx=False)
    settings = load_settings(app_cfg, agents_cfg)
    operational = build_operational_repositories(settings)

    intent = TxIntent(
        market_id="sandbox-intent-mkt-1",
        venue="polymarket",
        side=OutcomeSide.YES,
        stake_usd=120.0,
        limit_price=0.52,
        rationale="seed",
        run_id=deterministic_run_id,
        review_queue_id=f"{deterministic_run_id}:sandbox-intent-mkt-1:YES",
    )
    intent_id = _intent_id(intent)
    operational.transaction_intents.upsert_intent(deterministic_run_id, intent)
    operational.transaction_attempts.append_attempt(
        deterministic_run_id,
        TxAttempt(
            market_id=intent.market_id,
            execution_mode=ExecutionMode.SANDBOX_CHAIN,
            lane="sandbox_chain",
            intent_id=intent_id,
            venue=intent.venue,
            side=intent.side,
            stake_usd=intent.stake_usd,
            limit_price=intent.limit_price,
            status=ExecutionStatus.SUBMITTED,
            tx_hash="0xabc123",
            nonce=9,
            from_address="0x1111111111111111111111111111111111111111",
            chain_id=80_002,
            run_id=intent.run_id,
            review_queue_id=intent.review_queue_id,
            confirmation_status=TxConfirmationStatus.PENDING,
            submitted_at=datetime.now(UTC),
            message="seed_pending",
        ),
    )

    status_exit = main(
        [
            "tx-status",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--json",
        ]
    )
    assert status_exit == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert len(status_payload) == 1
    assert status_payload[0]["intent_id"] == intent_id
    assert status_payload[0]["confirmation_status"] == "PENDING"

    rpc_responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xabc123", "blockNumber": "0x10"}},
        {"jsonrpc": "2.0", "id": 1, "result": {"transactionHash": "0xabc123", "status": "0x1", "blockNumber": "0x10"}},
        {"jsonrpc": "2.0", "id": 1, "result": "0x10"},
    ]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        body = getattr(req, "data", b"").decode("utf-8")
        payload = json.loads(body)
        assert payload.get("method") in {"eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_blockNumber"}
        response = rpc_responses.pop(0)
        return _MockHttpResponse(json.dumps(response))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)
    reconcile_exit = main(
        [
            "tx-reconcile",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--json",
        ]
    )
    assert reconcile_exit == 0
    reconcile_payload = json.loads(capsys.readouterr().out)
    assert len(reconcile_payload) == 1
    assert reconcile_payload[0]["confirmation_status"] == "MINED"


def test_tx_resubmit_safe_command(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_sandbox_chain_enabled(app_cfg, submit_tx=True)
    settings = load_settings(app_cfg, agents_cfg)
    operational = build_operational_repositories(settings)

    intent = TxIntent(
        market_id="sandbox-intent-mkt-2",
        venue="polymarket",
        side=OutcomeSide.NO,
        stake_usd=90.0,
        limit_price=0.48,
        rationale="seed",
        run_id=deterministic_run_id,
        review_queue_id=f"{deterministic_run_id}:sandbox-intent-mkt-2:NO",
    )
    intent_id = _intent_id(intent)
    operational.transaction_intents.upsert_intent(deterministic_run_id, intent)
    operational.transaction_attempts.append_attempt(
        deterministic_run_id,
        TxAttempt(
            market_id=intent.market_id,
            execution_mode=ExecutionMode.SANDBOX_CHAIN,
            lane="sandbox_chain",
            intent_id=intent_id,
            venue=intent.venue,
            side=intent.side,
            stake_usd=intent.stake_usd,
            limit_price=intent.limit_price,
            status=ExecutionStatus.FAILED,
            tx_hash="0xdeadbeef",
            nonce=3,
            from_address="0x1111111111111111111111111111111111111111",
            chain_id=80_002,
            run_id=intent.run_id,
            review_queue_id=intent.review_queue_id,
            confirmation_status=TxConfirmationStatus.DROPPED,
            submitted_at=datetime.now(UTC),
            retry_count=0,
            message="seed_dropped",
        ),
    )

    rpc_responses = [
        {"jsonrpc": "2.0", "id": 1, "result": "0x1"},  # eth_call
        {"jsonrpc": "2.0", "id": 1, "result": "0x3"},  # eth_getTransactionCount
        {"jsonrpc": "2.0", "id": 1, "result": "0x3b9aca00"},  # eth_gasPrice
        {"jsonrpc": "2.0", "id": 1, "result": "0x5208"},  # eth_estimateGas
        {"jsonrpc": "2.0", "id": 1, "result": "0xbead1234"},  # eth_sendTransaction
    ]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        body = getattr(req, "data", b"").decode("utf-8")
        payload = json.loads(body)
        assert payload.get("method") in {
            "eth_call",
            "eth_getTransactionCount",
            "eth_gasPrice",
            "eth_estimateGas",
            "eth_sendTransaction",
        }
        response = rpc_responses.pop(0)
        return _MockHttpResponse(json.dumps(response))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)
    resubmit_exit = main(
        [
            "tx-resubmit-safe",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--intent-id",
            intent_id,
            "--json",
        ]
    )
    assert resubmit_exit == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["intent_id"] == intent_id
    assert payload[0]["latest_tx_hash"] == "0xbead1234"
    assert payload[0]["retry_count"] == 1
