from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import TxAttempt, TxIntent, TxReceipt
from prediction_market_bot.infrastructure.http_client import HttpClientError, StructuredHttpClient


class SandboxChainExecutor:
    """Safe transaction-plane executor for intent journaling on sandbox chains/testnets."""

    def __init__(
        self,
        *,
        rpc_url: str,
        contract_address: str,
        chain_id: int = 80_002,
        from_address: str = "",
        intent_method_selector: str = "0x6e6c3d69",
        submit_tx: bool = False,
        private_key: str = "",
        allow_unlocked_send: bool = True,
        gas_limit: int = 250_000,
        confirmations_required: int = 1,
        dropped_after_sec: int = 180,
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        enabled: bool = True,
        http_client: StructuredHttpClient | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.rpc_url = rpc_url.strip()
        self.contract_address = self._normalize_address(contract_address)
        self.chain_id = max(chain_id, 1)
        self.from_address = self._normalize_address(from_address)
        self.intent_method_selector = self._normalize_selector(intent_method_selector)
        self.submit_tx = bool(submit_tx)
        self.private_key = private_key.strip()
        self.allow_unlocked_send = bool(allow_unlocked_send)
        self.gas_limit = max(int(gas_limit), 21_000)
        self.confirmations_required = max(int(confirmations_required), 1)
        self.dropped_after_sec = max(int(dropped_after_sec), 1)
        self.timeout_sec = timeout_sec
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_sec = max(retry_backoff_sec, 0.0)
        self.retry_jitter_sec = max(retry_jitter_sec, 0.0)
        self.enabled = bool(enabled)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.http_client = http_client or StructuredHttpClient(
            user_agent="prediction-market-bot/0.1",
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            cache_ttl_sec=0,
        )
        self.last_attempt: TxAttempt | None = None
        if self.private_key and not self.from_address:
            try:
                account_module = self._load_eth_account()
                self.from_address = str(account_module.from_key(self.private_key).address)
            except Exception:
                # Defer signing failure to submit path with clear error.
                pass

    def place_order(self, order: TxIntent) -> TxReceipt:
        receipt, attempt = self._execute_intent(order)
        self.last_attempt = attempt
        return receipt

    def resubmit_safe(self, order: TxIntent, previous_attempt: TxAttempt | None) -> tuple[TxReceipt, TxAttempt]:
        if previous_attempt is not None and previous_attempt.confirmation_status in {
            TxConfirmationStatus.PENDING,
            TxConfirmationStatus.MINED,
        }:
            raise ValueError("resubmit_not_safe_pending_or_mined")
        retry_count = 1 if previous_attempt is None else previous_attempt.retry_count + 1
        replacement_for = previous_attempt.tx_hash if previous_attempt is not None else ""
        return self._execute_intent(
            order,
            replacement_for_tx_hash=replacement_for or "",
            retry_count=retry_count,
            replacement_nonce=previous_attempt.nonce if previous_attempt is not None else None,
        )

    def reconcile_attempt(self, attempt: TxAttempt) -> tuple[TxAttempt, TxReceipt]:
        now = self.now_fn()
        status = TxConfirmationStatus.UNKNOWN
        confirmed_at: datetime | None = None
        block_number: int | None = None
        rpc_error = ""
        receipt_payload: Mapping[str, Any] | None = None

        tx_hash = (attempt.tx_hash or "").strip()
        if not tx_hash:
            status = TxConfirmationStatus.UNKNOWN
            message = "tx_reconcile_missing_tx_hash"
        else:
            try:
                tx_payload = self._get_transaction_by_hash(tx_hash)
                receipt_payload = self._get_transaction_receipt(tx_hash)
                if receipt_payload is not None:
                    block_number = self._parse_hex_int(receipt_payload.get("blockNumber"))
                    receipt_status = self._parse_hex_int(receipt_payload.get("status"))
                    confirmations = self._confirmations(block_number)
                    if receipt_status == 0:
                        status = TxConfirmationStatus.FAILED
                        confirmed_at = now
                    elif confirmations >= self.confirmations_required:
                        status = TxConfirmationStatus.MINED
                        confirmed_at = now
                    else:
                        status = TxConfirmationStatus.PENDING
                    message = (
                        "tx_receipt_confirmed"
                        if status == TxConfirmationStatus.MINED
                        else "tx_receipt_pending_confirmations"
                    )
                elif tx_payload is not None:
                    tx_block = self._parse_hex_int(tx_payload.get("blockNumber"))
                    if tx_block is not None:
                        status = TxConfirmationStatus.PENDING
                        message = "tx_mined_waiting_receipt"
                    else:
                        status = TxConfirmationStatus.PENDING
                        message = "tx_pending"
                else:
                    status, message = self._resolve_missing_transaction_state(attempt, now)
            except Exception as exc:
                rpc_error = f"{type(exc).__name__}: {exc}"
                status = TxConfirmationStatus.UNKNOWN
                message = "tx_reconcile_rpc_error"

        updated_attempt = attempt.model_copy(
            update={
                "confirmation_status": status,
                "confirmed_at": confirmed_at,
                "message": (
                    message if not rpc_error else f"{message} error={rpc_error}"
                ),
                "metadata": tuple(
                    (
                        *attempt.metadata,
                        f"reconciled_at={now.isoformat()}",
                        f"confirmation_status={status.value}",
                    )
                ),
            }
        )
        tx_receipt = TxReceipt(
            market_id=attempt.market_id,
            execution_mode=ExecutionMode.SANDBOX_CHAIN,
            status=ExecutionStatus.FAILED if status == TxConfirmationStatus.FAILED else ExecutionStatus.SUBMITTED,
            side=attempt.side,
            stake_usd=attempt.stake_usd if status != TxConfirmationStatus.FAILED else 0.0,
            fill_price=None,
            order_id=f"sandbox-{attempt.intent_id}",
            intent_id=attempt.intent_id,
            tx_hash=attempt.tx_hash,
            nonce=attempt.nonce,
            submitted_at=attempt.submitted_at,
            confirmed_at=confirmed_at,
            confirmation_status=status,
            run_id=attempt.run_id,
            review_queue_id=attempt.review_queue_id,
            message=updated_attempt.message,
        )
        if block_number is not None:
            tx_receipt = tx_receipt.model_copy(
                update={"message": f"{tx_receipt.message} block_number={block_number}"}
            )
        if receipt_payload is not None:
            digest = hashlib.sha256(json.dumps(dict(receipt_payload), sort_keys=True, default=str).encode("utf-8")).hexdigest()
            updated_attempt = updated_attempt.model_copy(
                update={
                    "metadata": tuple((*updated_attempt.metadata, f"receipt_digest={digest}")),
                }
            )
        self.last_attempt = updated_attempt
        return updated_attempt, tx_receipt

    def _execute_intent(
        self,
        order: TxIntent,
        *,
        replacement_for_tx_hash: str = "",
        retry_count: int = 0,
        replacement_nonce: int | None = None,
    ) -> tuple[TxReceipt, TxAttempt]:
        intent_id = self._intent_id(order)
        if not self.enabled or not self.rpc_url or not self.contract_address:
            attempt = TxAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                lane="sandbox_chain",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=ExecutionStatus.SKIPPED,
                chain_id=self.chain_id,
                from_address=self.from_address,
                contract_address=self.contract_address,
                rpc_method="",
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.UNKNOWN,
                replacement_for_tx_hash=replacement_for_tx_hash,
                retry_count=retry_count,
                message="sandbox_chain_disabled_or_unconfigured",
            )
            receipt = TxReceipt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                status=ExecutionStatus.SKIPPED,
                side=order.side,
                stake_usd=0.0,
                order_id=f"sandbox-{intent_id}",
                intent_id=intent_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.UNKNOWN,
                message="sandbox_chain_disabled_or_unconfigured",
            )
            return receipt, attempt

        if self.submit_tx and (not order.run_id.strip() or not order.review_queue_id.strip()):
            attempt = TxAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                lane="sandbox_chain",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=ExecutionStatus.FAILED,
                chain_id=self.chain_id,
                from_address=self.from_address,
                contract_address=self.contract_address,
                rpc_method="eth_sendTransaction",
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                replacement_for_tx_hash=replacement_for_tx_hash,
                retry_count=retry_count,
                error_code="missing_traceability",
                error_message="run_id_or_review_queue_id_missing",
                message="sandbox_chain_traceability_required",
            )
            receipt = TxReceipt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                status=ExecutionStatus.FAILED,
                side=order.side,
                stake_usd=0.0,
                order_id=f"sandbox-{intent_id}",
                intent_id=intent_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                message="sandbox_chain_traceability_required",
            )
            return receipt, attempt

        tx_call = self._build_tx_call(order, intent_id=intent_id)
        now = self.now_fn()
        try:
            simulation = self._rpc(method="eth_call", params=[tx_call, "latest"])
            result_hex = self._extract_rpc_result(simulation)
            metadata = (
                "rpc_method=eth_call",
                f"result={result_hex[:42]}",
            )
            if not self.submit_tx:
                attempt = TxAttempt(
                    market_id=order.market_id,
                    execution_mode=ExecutionMode.SANDBOX_CHAIN,
                    lane="sandbox_chain",
                    intent_id=intent_id,
                    venue=order.venue,
                    side=order.side,
                    stake_usd=order.stake_usd,
                    limit_price=order.limit_price,
                    status=ExecutionStatus.SUBMITTED,
                    tx_hash=None,
                    chain_id=self.chain_id,
                    from_address=self.from_address,
                    contract_address=self.contract_address,
                    rpc_method="eth_call",
                    run_id=order.run_id,
                    review_queue_id=order.review_queue_id,
                    submitted_at=now,
                    confirmation_status=TxConfirmationStatus.UNKNOWN,
                    replacement_for_tx_hash=replacement_for_tx_hash,
                    retry_count=retry_count,
                    message="sandbox_chain_rehearsal_ok_no_submit",
                    metadata=metadata,
                )
                receipt = TxReceipt(
                    market_id=order.market_id,
                    execution_mode=ExecutionMode.SANDBOX_CHAIN,
                    status=ExecutionStatus.SUBMITTED,
                    side=order.side,
                    stake_usd=order.stake_usd,
                    fill_price=None,
                    order_id=f"sandbox-{intent_id}",
                    intent_id=intent_id,
                    tx_hash=None,
                    nonce=None,
                    submitted_at=now,
                    confirmed_at=None,
                    confirmation_status=TxConfirmationStatus.UNKNOWN,
                    run_id=order.run_id,
                    review_queue_id=order.review_queue_id,
                    message="sandbox_chain_rehearsal_ok_no_submit",
                )
                return receipt, attempt

            nonce = self._resolve_submission_nonce(replacement_nonce=replacement_nonce)
            gas_price_wei = self._parse_hex_int(self._rpc(method="eth_gasPrice", params=[]).get("result")) or 0
            gas_estimate = self._parse_hex_int(self._rpc(method="eth_estimateGas", params=[tx_call]).get("result")) or 0
            gas_limit = max(self.gas_limit, gas_estimate, 21_000)
            tx_hash = ""
            signed_payload_hash = ""
            signed_payload_meta: tuple[str, ...] = ()
            rpc_method = "eth_sendTransaction"

            if self.private_key:
                rpc_method = "eth_sendRawTransaction"
                sign_payload = self._build_sign_payload(
                    tx_call=tx_call,
                    nonce=nonce,
                    gas_limit=gas_limit,
                    gas_price_wei=gas_price_wei,
                )
                raw_tx, signed_payload_hash = self._sign_transaction(sign_payload)
                tx_hash = self._extract_rpc_result(
                    self._rpc(method="eth_sendRawTransaction", params=[raw_tx])
                )
                signed_payload_meta = (
                    "signer=private_key",
                    f"chain_id={self.chain_id}",
                    f"nonce={nonce}",
                    f"gas={gas_limit}",
                    f"gas_price_wei={gas_price_wei}",
                )
            elif self.allow_unlocked_send:
                if not self.from_address:
                    raise RuntimeError("sandbox_chain_from_address_required_for_unlocked_send")
                tx_payload = self._build_unlocked_tx_payload(
                    tx_call=tx_call,
                    nonce=nonce,
                    gas_limit=gas_limit,
                    gas_price_wei=gas_price_wei,
                )
                signed_payload_hash = hashlib.sha256(
                    json.dumps(tx_payload, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                tx_hash = self._extract_rpc_result(
                    self._rpc(method="eth_sendTransaction", params=[tx_payload])
                )
                signed_payload_meta = (
                    "signer=unlocked_account",
                    f"chain_id={self.chain_id}",
                    f"nonce={nonce}",
                    f"gas={gas_limit}",
                    f"gas_price_wei={gas_price_wei}",
                )
            else:
                raise RuntimeError("sandbox_chain_no_signing_path_configured")

            attempt = TxAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                lane="sandbox_chain",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=ExecutionStatus.SUBMITTED,
                tx_hash=tx_hash,
                nonce=nonce,
                chain_id=self.chain_id,
                from_address=self.from_address,
                contract_address=self.contract_address,
                rpc_method=rpc_method,
                signed_payload_hash=signed_payload_hash,
                signed_payload_metadata=signed_payload_meta,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                submitted_at=now,
                confirmation_status=TxConfirmationStatus.PENDING,
                replacement_for_tx_hash=replacement_for_tx_hash,
                retry_count=retry_count,
                message="sandbox_chain_tx_submitted",
                metadata=metadata,
            )
            receipt = TxReceipt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                status=ExecutionStatus.SUBMITTED,
                side=order.side,
                stake_usd=order.stake_usd,
                fill_price=None,
                order_id=f"sandbox-{intent_id}",
                intent_id=intent_id,
                tx_hash=tx_hash,
                nonce=nonce,
                submitted_at=now,
                confirmed_at=None,
                confirmation_status=TxConfirmationStatus.PENDING,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                message="sandbox_chain_tx_submitted",
            )
            return receipt, attempt
        except HttpClientError as exc:
            attempt = TxAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                lane="sandbox_chain",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=ExecutionStatus.FAILED,
                tx_hash=None,
                chain_id=self.chain_id,
                from_address=self.from_address,
                contract_address=self.contract_address,
                rpc_method="eth_call",
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                replacement_for_tx_hash=replacement_for_tx_hash,
                retry_count=retry_count,
                message="sandbox_chain_rehearsal_failed",
                error_code=exc.metadata.error_type,
                error_message=exc.metadata.message,
                metadata=(f"status_code={exc.metadata.status_code}",),
            )
            receipt = TxReceipt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                status=ExecutionStatus.FAILED,
                side=order.side,
                stake_usd=0.0,
                fill_price=None,
                order_id=f"sandbox-{intent_id}",
                intent_id=intent_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                message=f"sandbox_chain_rehearsal_failed error={exc.metadata.error_type}",
            )
            return receipt, attempt
        except Exception as exc:
            attempt = TxAttempt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                lane="sandbox_chain",
                intent_id=intent_id,
                venue=order.venue,
                side=order.side,
                stake_usd=order.stake_usd,
                limit_price=order.limit_price,
                status=ExecutionStatus.FAILED,
                tx_hash=None,
                chain_id=self.chain_id,
                from_address=self.from_address,
                contract_address=self.contract_address,
                rpc_method="eth_call",
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                replacement_for_tx_hash=replacement_for_tx_hash,
                retry_count=retry_count,
                message="sandbox_chain_rehearsal_failed",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            receipt = TxReceipt(
                market_id=order.market_id,
                execution_mode=ExecutionMode.SANDBOX_CHAIN,
                status=ExecutionStatus.FAILED,
                side=order.side,
                stake_usd=0.0,
                fill_price=None,
                order_id=f"sandbox-{intent_id}",
                intent_id=intent_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.FAILED,
                message=f"sandbox_chain_rehearsal_failed error={type(exc).__name__}",
            )
            return receipt, attempt

    def _resolve_submission_nonce(self, *, replacement_nonce: int | None) -> int:
        pending_nonce = self._get_pending_nonce(self.from_address)
        if replacement_nonce is None:
            return pending_nonce
        if pending_nonce <= replacement_nonce:
            return replacement_nonce
        return pending_nonce

    def _resolve_missing_transaction_state(self, attempt: TxAttempt, now: datetime) -> tuple[TxConfirmationStatus, str]:
        if attempt.nonce is not None and attempt.from_address:
            try:
                pending_nonce = self._get_pending_nonce(attempt.from_address)
                if pending_nonce > attempt.nonce:
                    return TxConfirmationStatus.REPLACED, "tx_replaced_by_higher_nonce_progress"
            except Exception:
                pass
        submitted = attempt.submitted_at
        if submitted is not None and now - submitted >= timedelta(seconds=self.dropped_after_sec):
            return TxConfirmationStatus.DROPPED, "tx_dropped_timeout_elapsed"
        return TxConfirmationStatus.PENDING, "tx_not_found_still_pending_window"

    def _confirmations(self, block_number: int | None) -> int:
        if block_number is None:
            return 0
        latest_block = self._parse_hex_int(self._rpc(method="eth_blockNumber", params=[]).get("result"))
        if latest_block is None:
            return 0
        return max(latest_block - block_number + 1, 0)

    def _get_pending_nonce(self, account: str) -> int:
        payload = self._rpc(method="eth_getTransactionCount", params=[account, "pending"])
        nonce = self._parse_hex_int(payload.get("result"))
        if nonce is None:
            raise RuntimeError("nonce_unavailable_from_rpc")
        return nonce

    def _get_transaction_by_hash(self, tx_hash: str) -> Mapping[str, Any] | None:
        payload = self._rpc(method="eth_getTransactionByHash", params=[tx_hash])
        result = payload.get("result")
        return dict(result) if isinstance(result, Mapping) else None

    def _get_transaction_receipt(self, tx_hash: str) -> Mapping[str, Any] | None:
        payload = self._rpc(method="eth_getTransactionReceipt", params=[tx_hash])
        result = payload.get("result")
        return dict(result) if isinstance(result, Mapping) else None

    def _build_sign_payload(
        self,
        *,
        tx_call: Mapping[str, str],
        nonce: int,
        gas_limit: int,
        gas_price_wei: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "to": str(tx_call.get("to", "")),
            "data": str(tx_call.get("data", "")),
            "value": 0,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": gas_price_wei,
            "chainId": self.chain_id,
        }
        if self.from_address:
            payload["from"] = self.from_address
        return payload

    def _build_unlocked_tx_payload(
        self,
        *,
        tx_call: Mapping[str, str],
        nonce: int,
        gas_limit: int,
        gas_price_wei: int,
    ) -> dict[str, str]:
        payload = {
            "from": self.from_address,
            "to": str(tx_call.get("to", "")),
            "data": str(tx_call.get("data", "")),
            "value": "0x0",
            "nonce": hex(nonce),
            "gas": hex(gas_limit),
            "gasPrice": hex(gas_price_wei),
        }
        return payload

    def _sign_transaction(self, tx_payload: Mapping[str, Any]) -> tuple[str, str]:
        try:
            account_module = self._load_eth_account()
        except Exception as exc:  # pragma: no cover - guarded in tests via config path
            raise RuntimeError("eth_account_dependency_missing_for_signing") from exc
        account = account_module.from_key(self.private_key)
        if self.from_address and account.address.lower() != self.from_address.lower():
            raise RuntimeError("from_address_does_not_match_private_key")
        signed = account.sign_transaction(dict(tx_payload))
        raw_tx = signed.raw_transaction.hex()
        raw_tx_hex = raw_tx if raw_tx.startswith("0x") else f"0x{raw_tx}"
        tx_hash_hex = signed.hash.hex()
        tx_hash_hex = tx_hash_hex if tx_hash_hex.startswith("0x") else f"0x{tx_hash_hex}"
        return raw_tx_hex, tx_hash_hex

    @staticmethod
    def _load_eth_account() -> Any:
        module = importlib.import_module("eth_account")
        return getattr(module, "Account")

    def _rpc(self, *, method: str, params: list[object]) -> Mapping[str, Any]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        response = self.http_client.fetch_json(
            source="sandbox_chain_rpc",
            url=self.rpc_url,
            method="POST",
            json_body=payload,
            use_cache=False,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
        )
        if isinstance(response.payload, Mapping):
            return dict(response.payload)
        raise RuntimeError("sandbox_chain_rpc_invalid_payload")

    @staticmethod
    def _extract_rpc_result(payload: Mapping[str, Any]) -> str:
        error_payload = payload.get("error")
        if isinstance(error_payload, Mapping):
            code = error_payload.get("code")
            message = error_payload.get("message")
            raise RuntimeError(f"rpc_error code={code} message={message}")
        result = payload.get("result")
        if isinstance(result, str):
            return result
        raise RuntimeError("rpc_result_missing_or_invalid")

    def _build_tx_call(self, order: TxIntent, *, intent_id: str) -> dict[str, str]:
        side_value = "1" if order.side == OutcomeSide.YES else "0"
        stake_cents = int(round(order.stake_usd * 100))
        limit_price_bps = int(round(order.limit_price * 10_000))
        calldata = self._encode_calldata(
            intent_id=intent_id,
            side=side_value,
            stake_cents=str(stake_cents),
            limit_price_bps=str(limit_price_bps),
        )
        payload = {
            "to": self.contract_address,
            "data": calldata,
            "value": "0x0",
        }
        if self.from_address:
            payload["from"] = self.from_address
        return payload

    def _encode_calldata(
        self,
        *,
        intent_id: str,
        side: str,
        stake_cents: str,
        limit_price_bps: str,
    ) -> str:
        selector = self.intent_method_selector[2:]
        args = [
            self._encode_bytes32(intent_id),
            self._encode_uint256(side),
            self._encode_uint256(stake_cents),
            self._encode_uint256(limit_price_bps),
        ]
        return f"0x{selector}{''.join(args)}"

    @staticmethod
    def _parse_hex_int(value: object) -> int | None:
        if not isinstance(value, str):
            return None
        text = value.strip().lower()
        if not text:
            return None
        try:
            if text.startswith("0x"):
                return int(text, 16)
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _encode_bytes32(value: str) -> str:
        text = value.lower().replace("0x", "").strip()
        if len(text) > 64:
            text = text[:64]
        return text.rjust(64, "0")

    @staticmethod
    def _encode_uint256(value: str) -> str:
        parsed = max(int(value), 0)
        return hex(parsed)[2:].rjust(64, "0")

    @staticmethod
    def _normalize_selector(value: str) -> str:
        text = value.strip().lower()
        if not text.startswith("0x"):
            text = f"0x{text}"
        raw = text[2:]
        if len(raw) < 8:
            raw = raw.rjust(8, "0")
        if len(raw) > 8:
            raw = raw[:8]
        return f"0x{raw}"

    @staticmethod
    def _normalize_address(value: str) -> str:
        text = value.strip()
        if not text:
            return ""
        if text.startswith("0x"):
            return text
        return f"0x{text}"

    @staticmethod
    def _intent_id(order: TxIntent) -> str:
        payload = (
            f"{order.run_id}|{order.review_queue_id}|{order.market_id}|{order.venue}|{order.side.value}|"
            f"{order.stake_usd:.6f}|{order.limit_price:.6f}"
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return digest
