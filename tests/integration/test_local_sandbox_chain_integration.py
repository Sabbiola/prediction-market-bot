from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from prediction_market_bot.domain.enums import ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import TxIntent
from prediction_market_bot.infrastructure.sandbox_chain import SandboxChainExecutor


class _RpcHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        method = payload.get("method")
        params = payload.get("params", [])

        result: object
        if method == "eth_call":
            result = "0x1"
        elif method == "eth_getTransactionCount":
            result = "0x1"
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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_sandbox_executor_works_with_local_rpc_chain() -> None:
    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), _RpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        executor = SandboxChainExecutor(
            rpc_url=f"http://127.0.0.1:{port}",
            contract_address="0x1234567890123456789012345678901234567890",
            from_address="0x1111111111111111111111111111111111111111",
            submit_tx=True,
            allow_unlocked_send=True,
            enabled=True,
        )
        intent = TxIntent(
            market_id="local-sandbox-market",
            venue="polymarket",
            side=OutcomeSide.YES,
            stake_usd=50.0,
            limit_price=0.6,
            rationale="local_chain_test",
            run_id="local-run",
            review_queue_id="local-run:local-sandbox-market:YES",
        )
        receipt = executor.place_order(intent)
        assert receipt.status is ExecutionStatus.SUBMITTED
        assert receipt.tx_hash == "0xlocaltx123"
        assert executor.last_attempt is not None
        updated_attempt, updated_receipt = executor.reconcile_attempt(executor.last_attempt)
        assert updated_attempt.confirmation_status is TxConfirmationStatus.MINED
        assert updated_receipt.confirmation_status is TxConfirmationStatus.MINED
    finally:
        server.shutdown()
        server.server_close()
