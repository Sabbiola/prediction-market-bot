"""Polymarket CLOB live executor with ERC-712 order signing.

This module provides ``PolymarketClobExecutor`` — the only executor that sends
real signed orders to ``clob.polymarket.com``.  All other executors (Paper,
ShadowSign, SandboxChain) are simulation-only.

Prerequisites
-------------
1. Install ``eth_account`` (included as an optional dependency):
   ``pip install eth-account``

2. Set the ``POLYMARKET_PRIVATE_KEY`` env var to the hex private key of a
   dedicated Polygon wallet (NOT your cold-storage key).

3. Fund the wallet with MATIC (gas) and USDC on Polygon mainnet.

4. Approve USDC for the Polymarket CTF Exchange contract:
   ``0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E``

5. Set ``polymarket_clob.dry_run: false`` in config ONLY after dress-rehearsal
   passes in SANDBOX_CHAIN mode.

ERC-712 domain
--------------
The Polymarket CTF Exchange uses the following typed-data domain on Polygon
mainnet (chain_id=137):
    {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": 137,
        "verifyingContract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    }

Order type hash (EIP-712 struct):
    Order(
        uint256 salt,
        address maker,
        address taker,
        address tokenId,      # outcome token ERC-1155 token ID
        uint256 makerAmount,  # USDC units (6 decimals)
        uint256 takerAmount,  # outcome token units (6 decimals)
        uint256 expiration,   # Unix ts; 0 = no expiry
        uint256 nonce,
        uint8   feeRateBps,
        uint8   side,         # 0 = BUY YES, 1 = BUY NO
        uint8   signatureType # 0 = EOA
    )

References
----------
- https://docs.polymarket.com/developers/CLOB/orders
- https://github.com/Polymarket/py-clob-client
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, TxConfirmationStatus
from prediction_market_bot.domain.models import ExecutionResult, OrderIntent, TransactionAttempt
from prediction_market_bot.interfaces import TradeExecutor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USDC_DECIMALS = 6
_OUTCOME_TOKEN_DECIMALS = 6
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# ERC-712 type strings (must match on-chain ABI exactly)
_ORDER_TYPE = (
    "Order(uint256 salt,address maker,address taker,address tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint256 expiration,"
    "uint256 nonce,uint256 feeRateBps,uint8 side,uint8 signatureType)"
)

# Side encoding: Polymarket uses 0 for BUY (YES or NO token)
_SIDE_BUY = 0

# SignatureType: 0 = EOA (regular private key), 1 = EIP1271 (smart contract)
_SIG_TYPE_EOA = 0


# ---------------------------------------------------------------------------
# Optional eth_account import
# ---------------------------------------------------------------------------

def _require_eth_account() -> Any:
    """Import ``eth_account`` or raise a clear error if not installed."""
    try:
        from eth_account import Account  # type: ignore[import-untyped]
        from eth_account.messages import encode_typed_data  # type: ignore[import-untyped]
        return Account, encode_typed_data
    except ImportError as exc:
        raise ImportError(
            "PolymarketClobExecutor requires the 'eth_account' package. "
            "Install it with:  pip install eth-account>=0.11"
        ) from exc


# ---------------------------------------------------------------------------
# Order builder
# ---------------------------------------------------------------------------

@dataclass
class _SignedOrder:
    """Fully constructed, signed Polymarket order ready for CLOB submission."""

    salt: int
    maker: str
    taker: str
    token_id: str           # ERC-1155 outcome token ID (YES or NO)
    maker_amount: int       # USDC units
    taker_amount: int       # outcome token units
    expiration: int
    nonce: int
    fee_rate_bps: int
    side: int
    signature_type: int
    signature: str          # 0x-prefixed hex
    order_type: str = "GTC"
    _raw: dict[str, Any] = field(default_factory=dict)

    def to_api_payload(self) -> dict[str, Any]:
        """Serialize to the shape expected by clob.polymarket.com/orders."""
        return {
            "order": {
                "salt": str(self.salt),
                "maker": self.maker,
                "taker": self.taker,
                "tokenId": self.token_id,
                "makerAmount": str(self.maker_amount),
                "takerAmount": str(self.taker_amount),
                "expiration": str(self.expiration),
                "nonce": str(self.nonce),
                "feeRateBps": str(self.fee_rate_bps),
                "side": str(self.side),
                "signatureType": str(self.signature_type),
                "signature": self.signature,
            },
            "owner": self.maker,
            "orderType": self.order_type,
        }


def _to_usdc_units(amount_usd: float) -> int:
    """Convert float USD amount to USDC integer units (6 decimals)."""
    return int(round(amount_usd * (10 ** _USDC_DECIMALS)))


def _to_token_units(amount: float) -> int:
    """Convert float outcome token amount to integer units (6 decimals)."""
    return int(round(amount * (10 ** _OUTCOME_TOKEN_DECIMALS)))


def _build_eip712_domain(chain_id: int, contract_address: str) -> dict[str, Any]:
    return {
        "name": "Polymarket CTF Exchange",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": contract_address,
    }


def _build_eip712_message(
    *,
    maker: str,
    token_id: str,
    side: int,
    maker_amount: int,
    taker_amount: int,
    fee_rate_bps: int,
    expiration: int,
    nonce: int,
    salt: int,
) -> dict[str, Any]:
    return {
        "salt": salt,
        "maker": maker,
        "taker": _ZERO_ADDRESS,
        "tokenId": int(token_id, 16) if token_id.startswith("0x") else int(token_id),
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": expiration,
        "nonce": nonce,
        "feeRateBps": fee_rate_bps,
        "side": side,
        "signatureType": _SIG_TYPE_EOA,
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class PolymarketClobExecutor(TradeExecutor):
    """Live Polymarket CLOB executor — submits ERC-712 signed orders.

    Safety defaults:
    * ``dry_run=True`` (default): signs the order but does NOT submit it.
      Set ``dry_run=False`` only when live trading is fully tested.
    * The private key is NEVER stored in settings YAML; it is read once from
      the env var named by ``private_key_env`` at construction time.
    * Submission errors do NOT raise; they return ``ExecutionStatus.FAILED``
      with a human-readable message so the pipeline can continue.
    """

    def __init__(
        self,
        *,
        clob_endpoint_url: str = "https://clob.polymarket.com",
        chain_id: int = 137,
        exchange_contract_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        private_key_env: str = "POLYMARKET_PRIVATE_KEY",
        wallet_address: str = "",
        slippage_bps: int = 50,
        fee_rate_bps: int = 72,
        max_taker_fee_bps: int = 100,
        timeout_sec: float = 10.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 1.0,
        dry_run: bool = True,
    ) -> None:
        self._clob_url = clob_endpoint_url.rstrip("/")
        self._chain_id = chain_id
        self._contract_address = exchange_contract_address
        self._slippage_bps = max(slippage_bps, 0)
        self._fee_rate_bps = max(fee_rate_bps, 0)
        self._max_taker_fee_bps = max(max_taker_fee_bps, 0)
        self._timeout_sec = max(timeout_sec, 1.0)
        self._max_retries = max(max_retries, 0)
        self._retry_backoff_sec = max(retry_backoff_sec, 0.0)
        self._dry_run = dry_run
        self.last_attempt: TransactionAttempt | None = None

        # Resolve private key from environment — never from config YAML.
        private_key = os.environ.get(private_key_env, "").strip()
        if not private_key and not dry_run:
            raise ValueError(
                f"PolymarketClobExecutor: env var '{private_key_env}' is not set. "
                "Live order submission requires a funded Polygon private key."
            )

        self._private_key: str = private_key

        # Derive wallet address from private key (or accept override).
        self._wallet_address: str = wallet_address.strip()
        if self._private_key and not self._wallet_address:
            try:
                Account, _ = _require_eth_account()
                acct = Account.from_key(self._private_key)
                self._wallet_address = acct.address
            except ImportError:
                pass  # Will fail at sign time; handled there

    # ------------------------------------------------------------------
    # TradeExecutor interface
    # ------------------------------------------------------------------

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        """Build, sign, and submit (or dry-run) a Polymarket CLOB order."""
        submitted_at = datetime.now(UTC)
        try:
            result = self._execute(order, submitted_at=submitted_at)
        except Exception as exc:
            logger.error(
                "polymarket_clob_unexpected_error",
                extra={
                    "event": "polymarket_clob_unexpected_error",
                    "market_id": order.market_id,
                    "error": str(exc),
                },
            )
            result = self._failed_result(order, f"unexpected_error: {exc}", submitted_at)
        self.last_attempt = self._build_attempt(order, result)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, order: OrderIntent, *, submitted_at: datetime) -> ExecutionResult:
        if not self._private_key:
            # No key configured — dry-run only, return SKIPPED to make
            # clear that no actual order was produced.
            return ExecutionResult(
                market_id=order.market_id,
                execution_mode=ExecutionMode.POLYMARKET_LIVE,
                status=ExecutionStatus.SKIPPED,
                side=order.side,
                stake_usd=0.0,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.UNKNOWN,
                message="polymarket_clob_skipped_no_private_key",
                submitted_at=submitted_at,
            )

        # Fetch token IDs for the market (YES and NO outcome token addresses).
        token_id = self._resolve_token_id(order)
        if token_id is None:
            return self._failed_result(
                order,
                "polymarket_clob_token_id_resolution_failed",
                submitted_at,
            )

        # Build ERC-712 signed order.
        try:
            signed = self._build_signed_order(order, token_id=token_id)
        except Exception as exc:
            logger.error(
                "polymarket_clob_sign_failed",
                extra={"event": "polymarket_clob_sign_failed", "market_id": order.market_id, "error": str(exc)},
            )
            return self._failed_result(order, f"sign_failed: {exc}", submitted_at)

        if self._dry_run:
            logger.info(
                "polymarket_clob_dry_run_order_built",
                extra={
                    "event": "polymarket_clob_dry_run_order_built",
                    "market_id": order.market_id,
                    "side": order.side.value,
                    "stake_usd": order.stake_usd,
                    "token_id": token_id,
                    "maker_amount": signed.maker_amount,
                    "taker_amount": signed.taker_amount,
                    "dry_run": True,
                },
            )
            return ExecutionResult(
                market_id=order.market_id,
                execution_mode=ExecutionMode.POLYMARKET_LIVE,
                status=ExecutionStatus.SUBMITTED,
                side=order.side,
                stake_usd=order.stake_usd,
                fill_price=order.limit_price,
                order_id=f"dry-run-{order.market_id[:8]}",
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.PENDING,
                message="polymarket_clob_dry_run_signed_not_submitted",
                submitted_at=submitted_at,
            )

        # Real submission.
        api_payload = signed.to_api_payload()
        order_id = self._submit_order(api_payload)

        if order_id:
            logger.info(
                "polymarket_clob_order_submitted",
                extra={
                    "event": "polymarket_clob_order_submitted",
                    "market_id": order.market_id,
                    "order_id": order_id,
                    "side": order.side.value,
                    "stake_usd": order.stake_usd,
                },
            )
            return ExecutionResult(
                market_id=order.market_id,
                execution_mode=ExecutionMode.POLYMARKET_LIVE,
                status=ExecutionStatus.FILLED,
                side=order.side,
                stake_usd=order.stake_usd,
                fill_price=order.limit_price,
                order_id=order_id,
                run_id=order.run_id,
                review_queue_id=order.review_queue_id,
                confirmation_status=TxConfirmationStatus.MINED,
                message="polymarket_clob_order_filled",
                submitted_at=submitted_at,
                confirmed_at=datetime.now(UTC),
            )
        return self._failed_result(order, "polymarket_clob_submission_returned_no_order_id", submitted_at)

    def _resolve_token_id(self, order: OrderIntent) -> str | None:
        """Fetch the outcome token ID for this market + side from Polymarket API."""
        url = f"https://gamma-api.polymarket.com/markets/{order.market_id}"
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "prediction-market-bot/0.1"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:  # nosec B310
                raw = json.loads(resp.read(524_288))

            from prediction_market_bot.domain.enums import OutcomeSide
            # Polymarket markets have a ``clobTokenIds`` array: [YES_token_id, NO_token_id]
            tokens = []
            if isinstance(raw, dict):
                tokens = raw.get("clobTokenIds") or []
            elif isinstance(raw, list) and raw:
                tokens = (raw[0].get("clobTokenIds") or []) if isinstance(raw[0], dict) else []

            if len(tokens) >= 2:
                # tokens[0] = YES, tokens[1] = NO
                idx = 0 if order.side == OutcomeSide.YES else 1
                return str(tokens[idx])

            logger.warning(
                "polymarket_clob_token_resolution_no_tokens",
                extra={
                    "event": "polymarket_clob_token_resolution_no_tokens",
                    "market_id": order.market_id,
                    "response_keys": list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
                },
            )
            return None
        except Exception as exc:
            logger.warning(
                "polymarket_clob_token_resolution_failed",
                extra={
                    "event": "polymarket_clob_token_resolution_failed",
                    "market_id": order.market_id,
                    "error": str(exc),
                },
            )
            return None

    def _build_signed_order(self, order: OrderIntent, *, token_id: str) -> _SignedOrder:
        Account, encode_typed_data = _require_eth_account()

        # Compute amounts.
        # BUY order: maker pays USDC (makerAmount), receives outcome tokens (takerAmount).
        # limit_price = cost per outcome token (0.0–1.0).
        limit_price = max(min(float(order.limit_price), 0.999), 0.001)
        # Apply slippage tolerance: willing to pay slightly more than quoted price.
        max_price = min(limit_price * (1 + self._slippage_bps / 10_000), 0.999)
        maker_amount = _to_usdc_units(order.stake_usd)
        # takerAmount = stake / max_price (how many tokens we expect to receive)
        taker_amount = _to_token_units(order.stake_usd / max_price)

        salt = int(time.time() * 1_000_000) % (2**64)
        expiration = 0  # GTC (good till cancelled)
        nonce = 0

        message = _build_eip712_message(
            maker=self._wallet_address,
            token_id=token_id,
            side=_SIDE_BUY,
            maker_amount=maker_amount,
            taker_amount=taker_amount,
            fee_rate_bps=self._fee_rate_bps,
            expiration=expiration,
            nonce=nonce,
            salt=salt,
        )
        domain = _build_eip712_domain(self._chain_id, self._contract_address)

        # ERC-712 typed data signing.
        structured_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Order": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                    {"name": "taker", "type": "address"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "makerAmount", "type": "uint256"},
                    {"name": "takerAmount", "type": "uint256"},
                    {"name": "expiration", "type": "uint256"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "feeRateBps", "type": "uint256"},
                    {"name": "side", "type": "uint8"},
                    {"name": "signatureType", "type": "uint8"},
                ],
            },
            "domain": domain,
            "primaryType": "Order",
            "message": message,
        }

        signed_msg = Account.sign_message(
            encode_typed_data(full_message=structured_data),
            private_key=self._private_key,
        )
        signature_hex = signed_msg.signature.hex()
        if not signature_hex.startswith("0x"):
            signature_hex = "0x" + signature_hex

        return _SignedOrder(
            salt=salt,
            maker=self._wallet_address,
            taker=_ZERO_ADDRESS,
            token_id=token_id,
            maker_amount=maker_amount,
            taker_amount=taker_amount,
            expiration=expiration,
            nonce=nonce,
            fee_rate_bps=self._fee_rate_bps,
            side=_SIDE_BUY,
            signature_type=_SIG_TYPE_EOA,
            signature=signature_hex,
        )

    def _submit_order(self, payload: dict[str, Any]) -> str | None:
        """POST signed order to CLOB and return the order_id string, or None."""
        url = f"{self._clob_url}/orders"
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "prediction-market-bot/0.1",
        }
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:  # nosec B310
                    resp_data = json.loads(resp.read(524_288))
                # Success — extract order_id from response.
                if isinstance(resp_data, dict):
                    oid = str(resp_data.get("orderId") or resp_data.get("order_id") or "").strip()
                    if oid:
                        return oid
                    # Some CLOB responses nest under "orderCreated" or similar.
                    for key in ("orderCreated", "order"):
                        nested = resp_data.get(key)
                        if isinstance(nested, dict):
                            oid = str(nested.get("id") or nested.get("orderId") or "").strip()
                            if oid:
                                return oid
                logger.warning(
                    "polymarket_clob_submit_no_order_id",
                    extra={"event": "polymarket_clob_submit_no_order_id", "response": str(resp_data)[:200]},
                )
                return None
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                last_exc = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 401, 403, 422}:
                    # Non-retryable client errors.
                    logger.error(
                        "polymarket_clob_submit_client_error",
                        extra={
                            "event": "polymarket_clob_submit_client_error",
                            "status": exc.code,
                            "error": str(exc),
                        },
                    )
                    return None
                if attempt < self._max_retries:
                    backoff = self._retry_backoff_sec * (attempt + 1)
                    logger.warning(
                        "polymarket_clob_submit_retrying",
                        extra={
                            "event": "polymarket_clob_submit_retrying",
                            "attempt": attempt + 1,
                            "backoff_sec": backoff,
                            "error": str(exc),
                        },
                    )
                    time.sleep(backoff)
        if last_exc is not None:
            logger.error(
                "polymarket_clob_submit_exhausted_retries",
                extra={
                    "event": "polymarket_clob_submit_exhausted_retries",
                    "error": str(last_exc),
                    "retries": self._max_retries,
                },
            )
        return None

    @staticmethod
    def _failed_result(
        order: OrderIntent,
        message: str,
        submitted_at: datetime,
    ) -> ExecutionResult:
        return ExecutionResult(
            market_id=order.market_id,
            execution_mode=ExecutionMode.POLYMARKET_LIVE,
            status=ExecutionStatus.FAILED,
            side=order.side,
            stake_usd=0.0,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            confirmation_status=TxConfirmationStatus.FAILED,
            message=message,
            submitted_at=submitted_at,
        )

    @staticmethod
    def _intent_id(order: OrderIntent) -> str:
        """Stable content-hash for an order intent (mirrors agents.executors._intent_id)."""
        import hashlib
        payload = (
            f"{order.run_id}|{order.review_queue_id}|{order.market_id}|{order.venue}"
            f"|{order.side.value}|{order.stake_usd:.6f}|{order.limit_price:.6f}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @staticmethod
    def _build_attempt(order: OrderIntent, result: ExecutionResult) -> TransactionAttempt:
        return TransactionAttempt(
            market_id=order.market_id,
            execution_mode=ExecutionMode.POLYMARKET_LIVE,
            lane="main",
            intent_id=PolymarketClobExecutor._intent_id(order),
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=result.status,
            tx_hash=None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=result.submitted_at,
            confirmed_at=result.confirmed_at,
            confirmation_status=result.confirmation_status,
            message=result.message,
        )
