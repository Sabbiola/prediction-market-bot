from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import OrderIntent, TxAttempt
from prediction_market_bot.infrastructure import http_client as http_client_module
from prediction_market_bot.infrastructure.sandbox_chain import SandboxChainExecutor


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


def _order() -> OrderIntent:
    return OrderIntent(
        market_id="sandbox-mkt-1",
        venue="polymarket",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        limit_price=0.55,
        rationale="unit_test",
        run_id="run-1",
        review_queue_id="run-1:sandbox-mkt-1:YES",
    )


def test_sandbox_executor_skips_when_unconfigured() -> None:
    executor = SandboxChainExecutor(
        rpc_url="",
        contract_address="",
        enabled=False,
    )

    result = executor.place_order(_order())
    assert result.execution_mode is ExecutionMode.SANDBOX_CHAIN
    assert result.status is ExecutionStatus.SKIPPED
    assert executor.last_attempt is not None
    assert executor.last_attempt.status is ExecutionStatus.SKIPPED


def test_sandbox_executor_rehearses_with_eth_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x1"}))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    executor = SandboxChainExecutor(
        rpc_url="https://amoy.example/rpc",
        contract_address="0x1234567890123456789012345678901234567890",
        chain_id=80_002,
        enabled=True,
        submit_tx=False,
    )
    result = executor.place_order(_order())

    assert result.execution_mode is ExecutionMode.SANDBOX_CHAIN
    assert result.status is ExecutionStatus.SUBMITTED
    assert result.order_id is not None
    assert result.order_id.startswith("sandbox-")
    assert executor.last_attempt is not None
    assert executor.last_attempt.status is ExecutionStatus.SUBMITTED
    assert executor.last_attempt.rpc_method == "eth_call"


def test_sandbox_executor_returns_failed_on_rpc_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del req, timeout
        return _MockHttpResponse(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32000, "message": "execution reverted"},
                }
            )
        )

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    executor = SandboxChainExecutor(
        rpc_url="https://amoy.example/rpc",
        contract_address="0x1234567890123456789012345678901234567890",
        enabled=True,
    )
    result = executor.place_order(_order())

    assert result.status is ExecutionStatus.FAILED
    assert executor.last_attempt is not None
    assert executor.last_attempt.status is ExecutionStatus.FAILED
    assert executor.last_attempt.error_code == "RuntimeError"


def test_sandbox_executor_submits_real_tx_via_unlocked_account(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": "0x1"},  # eth_call
        {"jsonrpc": "2.0", "id": 1, "result": "0x9"},  # eth_getTransactionCount
        {"jsonrpc": "2.0", "id": 1, "result": "0x3b9aca00"},  # eth_gasPrice
        {"jsonrpc": "2.0", "id": 1, "result": "0x5208"},  # eth_estimateGas
        {"jsonrpc": "2.0", "id": 1, "result": "0xabc123"},  # eth_sendTransaction
    ]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        body = getattr(req, "data", b"").decode("utf-8")
        payload = json.loads(body)
        method = payload.get("method")
        expected = responses.pop(0)
        assert method in {"eth_call", "eth_getTransactionCount", "eth_gasPrice", "eth_estimateGas", "eth_sendTransaction"}
        return _MockHttpResponse(json.dumps(expected))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    executor = SandboxChainExecutor(
        rpc_url="https://amoy.example/rpc",
        contract_address="0x1234567890123456789012345678901234567890",
        chain_id=80_002,
        from_address="0x1111111111111111111111111111111111111111",
        enabled=True,
        submit_tx=True,
        allow_unlocked_send=True,
        private_key="",
    )
    result = executor.place_order(_order())

    assert result.execution_mode is ExecutionMode.SANDBOX_CHAIN
    assert result.status is ExecutionStatus.SUBMITTED
    assert result.tx_hash == "0xabc123"
    assert result.nonce == 9
    assert result.confirmation_status is TxConfirmationStatus.PENDING
    assert executor.last_attempt is not None
    assert executor.last_attempt.rpc_method == "eth_sendTransaction"
    assert executor.last_attempt.signed_payload_hash
    assert executor.last_attempt.run_id == "run-1"
    assert executor.last_attempt.review_queue_id == "run-1:sandbox-mkt-1:YES"


def test_sandbox_executor_blocks_submit_when_traceability_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_client_module.request, "urlopen", lambda req, timeout: _MockHttpResponse("{}"))
    executor = SandboxChainExecutor(
        rpc_url="https://amoy.example/rpc",
        contract_address="0x1234567890123456789012345678901234567890",
        from_address="0x1111111111111111111111111111111111111111",
        enabled=True,
        submit_tx=True,
    )
    order = _order().model_copy(update={"run_id": "", "review_queue_id": ""})
    result = executor.place_order(order)
    assert result.status is ExecutionStatus.FAILED
    assert result.message == "sandbox_chain_traceability_required"


def test_sandbox_executor_reconcile_replaced_when_nonce_advanced(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": None},  # tx by hash
        {"jsonrpc": "2.0", "id": 1, "result": None},  # receipt by hash
        {"jsonrpc": "2.0", "id": 1, "result": "0x0c"},  # pending nonce
    ]

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        body = getattr(req, "data", b"").decode("utf-8")
        payload = json.loads(body)
        assert payload.get("method") in {"eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_getTransactionCount"}
        expected = responses.pop(0)
        return _MockHttpResponse(json.dumps(expected))

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)
    executor = SandboxChainExecutor(
        rpc_url="https://amoy.example/rpc",
        contract_address="0x1234567890123456789012345678901234567890",
        from_address="0x1111111111111111111111111111111111111111",
        enabled=True,
        submit_tx=True,
    )
    attempt = TxAttempt(
        market_id="sandbox-mkt-1",
        execution_mode=ExecutionMode.SANDBOX_CHAIN,
        lane="sandbox_chain",
        intent_id="intent-1",
        venue="polymarket",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        limit_price=0.55,
        status=ExecutionStatus.SUBMITTED,
        tx_hash="0xabc123",
        nonce=9,
        from_address="0x1111111111111111111111111111111111111111",
        chain_id=80_002,
        confirmation_status=TxConfirmationStatus.PENDING,
        run_id="run-1",
        review_queue_id="q-1",
        submitted_at=datetime.now(UTC),
    )
    updated, receipt = executor.reconcile_attempt(attempt)
    assert updated.confirmation_status is TxConfirmationStatus.REPLACED
    assert receipt.confirmation_status is TxConfirmationStatus.REPLACED
