"""Minimal JSON-RPC mock server for staging dress rehearsal.

Emulates a testnet node: mines every submitted TX immediately.
Usage: python scripts/mock_rpc_server.py [--port 18545]
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class _MockRpcHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        method = payload.get("method")
        params = payload.get("params", [])

        if method == "eth_call":
            result: object = "0x1"
        elif method == "eth_getTransactionCount":
            result = "0x2"
        elif method == "eth_gasPrice":
            result = "0x3b9aca00"
        elif method == "eth_estimateGas":
            result = "0x5208"
        elif method == "eth_sendTransaction":
            result = "0xmocktx" + "0" * 58
        elif method == "eth_sendRawTransaction":
            result = "0xmocktx" + "0" * 58
        elif method == "eth_getTransactionByHash":
            tx_hash = params[0] if params else "0x0"
            result = {"hash": tx_hash, "blockNumber": "0x10"}
        elif method == "eth_getTransactionReceipt":
            tx_hash = params[0] if params else "0x0"
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18545)
    args = parser.parse_args()
    server = HTTPServer(("127.0.0.1", args.port), _MockRpcHandler)
    print(f"Mock RPC server listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
