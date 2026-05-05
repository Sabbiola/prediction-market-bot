"""HyperliquidExecutor — translates a YES/NO OrderIntent into a BTC perp
long/short order on Hyperliquid via the SDK wrapper.

Mapping rules:
- side YES (= "BTC will close higher this slot")  -> LONG  BTC perp
- side NO  (= "BTC will close lower this slot")   -> SHORT BTC perp
- stake_usd at leverage L gives  size_btc = (stake_usd * L) / mark_px
- Take-profit / stop-loss are placed atomically with the parent order so
  the position is bounded even if the bot or the network is unreachable
  for the rest of the 15-minute slot.
- A time-based force-close runs each scheduler tick: any open position whose
  hold_seconds exceeds time_exit_seconds is market-closed.  This aligns the
  HL position window with the 15-minute prediction horizon — without it the
  TP/SL could remain open for hours, decoupled from the model signal.

Implements the same TradeExecutor interface as PaperExecutor so the
ExecutionAgent doesn't need any orchestration changes — only the
``execution.mode`` config switches to ``HYPERLIQUID_*`` and bootstrap
wires this class.

Modes:
- HYPERLIQUID_DRY_RUN: synthesize an OK ExecutionResult, no network I/O
- HYPERLIQUID_TESTNET: live testnet orders (mock USDC), useful for E2E
- HYPERLIQUID_LIVE: real money — gated separately by config
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prediction_market_bot.domain.enums import (
    ExecutionMode,
    ExecutionStatus,
    OutcomeSide,
    TxConfirmationStatus,
)
from prediction_market_bot.domain.models import (
    ExecutionResult,
    OrderIntent,
    TransactionAttempt,
)
from prediction_market_bot.infrastructure.hyperliquid_client import (
    HyperliquidClient,
    HyperliquidConfig,
    OrderResult,
)

logger = logging.getLogger(__name__)


def _intent_id(order: OrderIntent) -> str:
    payload = (
        f"{order.run_id}|{order.review_queue_id}|{order.market_id}|{order.venue}|{order.side.value}|"
        f"{order.stake_usd:.6f}|{order.limit_price:.6f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class _OpenPosition:
    intent_id: str
    market_id: str
    is_long: bool
    size_btc: float
    entry_px: float
    opened_at: datetime
    leverage: int


class HyperliquidExecutor:
    """Routes YES/NO orders to Hyperliquid BTC perp longs/shorts."""

    def __init__(
        self,
        *,
        execution_mode: ExecutionMode,
        client: HyperliquidClient,
        leverage: int = 3,
        # Symmetric-ish TP/SL aligned with BTC 15m volatility (~0.25-0.4%).
        # +0.30% TP / -0.20% SL → break-even win rate = 0.20/(0.30+0.20) = 40%.
        # Model historical win rate ≈ 45% → positive expected value.
        take_profit_pct: float = 0.0030,   # +0.30% from entry
        stop_loss_pct: float = 0.0020,     # -0.20% from entry
        time_exit_seconds: int = 900,       # close after 15 minutes
        min_order_usd: float = 10.0,
        max_size_usd: float = 100.0,        # safety cap per trade
    ) -> None:
        if execution_mode not in (
            ExecutionMode.HYPERLIQUID_DRY_RUN,
            ExecutionMode.HYPERLIQUID_TESTNET,
            ExecutionMode.HYPERLIQUID_LIVE,
        ):
            raise ValueError(f"unsupported_execution_mode:{execution_mode}")
        self.execution_mode = execution_mode
        self.client = client
        self.leverage = max(1, int(leverage))
        self.take_profit_pct = float(take_profit_pct)
        self.stop_loss_pct = float(stop_loss_pct)
        self.time_exit_seconds = int(time_exit_seconds)
        self.min_order_usd = float(min_order_usd)
        self.max_size_usd = float(max_size_usd)
        self.last_attempt: TransactionAttempt | None = None
        self._open_positions: list[_OpenPosition] = []
        self._lock = threading.Lock()

    # ── interface required by ExecutionAgent ────────────────────────────

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        intent_id = _intent_id(order)
        now = datetime.now(UTC)

        # Force-close any expired positions first so capital is freed.
        self._close_expired_positions(now=now)

        # Hyperliquid's true minimum is on NOTIONAL (stake * leverage), not stake.
        # E.g. $4 at 3x leverage = $12 notional which clears the $10 floor.
        notional = min(order.stake_usd * self.leverage, self.max_size_usd * self.leverage)
        if notional < self.min_order_usd:
            return self._fail(
                order, intent_id,
                f"notional_below_min_order_usd ({notional:.2f} < {self.min_order_usd:.2f})",
            )

        snap = self.client.fetch_market_snapshot()
        if snap is None or snap.mark_px <= 0:
            return self._fail(order, intent_id, "no_market_snapshot")

        # Side mapping: YES -> LONG, NO -> SHORT.
        is_long = order.side == OutcomeSide.YES
        # Convert USD notional to BTC size at the current mark.
        size_btc = self.client.round_size(notional / snap.mark_px)
        if size_btc <= 0:
            return self._fail(order, intent_id, f"size_rounded_to_zero (notional={notional:.2f})")

        # TP/SL relative to mark, sign flipped depending on direction.
        if is_long:
            tp_px = round(snap.mark_px * (1 + self.take_profit_pct), 1)
            sl_px = round(snap.mark_px * (1 - self.stop_loss_pct), 1)
        else:
            tp_px = round(snap.mark_px * (1 - self.take_profit_pct), 1)
            sl_px = round(snap.mark_px * (1 + self.stop_loss_pct), 1)

        result: OrderResult = self.client.market_order_with_tpsl(
            is_long=is_long,
            size_btc=size_btc,
            leverage=self.leverage,
            take_profit_px=tp_px,
            stop_loss_px=sl_px,
        )

        if not result.ok:
            return self._fail(order, intent_id, f"hl_order_rejected:{result.error}")

        # Track this position for the time-exit watchdog.
        with self._lock:
            self._open_positions.append(_OpenPosition(
                intent_id=intent_id,
                market_id=order.market_id,
                is_long=is_long,
                size_btc=size_btc,
                entry_px=result.avg_fill_px or snap.mark_px,
                opened_at=now,
                leverage=self.leverage,
            ))

        # Map status: dry_run / filled / resting → FILLED for our pipeline
        # (the order was accepted; settlement will reconcile later from HL state).
        status = ExecutionStatus.FILLED
        confirmation = (
            TxConfirmationStatus.MINED
            if result.status == "filled"
            else TxConfirmationStatus.UNKNOWN
        )
        avg_px = result.avg_fill_px if result.avg_fill_px else snap.mark_px
        order_id = (
            f"hl-{result.order_id}" if result.order_id else
            f"hl-{self.execution_mode.value.lower()}-{intent_id[:12]}"
        )

        message_parts = [
            f"venue=hyperliquid",
            f"mode={self.execution_mode.value}",
            f"side={'LONG' if is_long else 'SHORT'}",
            f"size_btc={size_btc:.5f}",
            f"mark_px={snap.mark_px:.1f}",
            f"avg_px={avg_px:.1f}" if avg_px else "avg_px=-",
            f"tp={tp_px:.1f}",
            f"sl={sl_px:.1f}",
            f"leverage={self.leverage}x",
            f"time_exit_s={self.time_exit_seconds}",
            f"hl_status={result.status}",
        ]
        message = " ".join(message_parts)

        execution = ExecutionResult(
            market_id=order.market_id,
            execution_mode=self.execution_mode,
            status=status,
            side=order.side,
            stake_usd=order.stake_usd,
            order_id=order_id,
            intent_id=intent_id,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            confirmation_status=confirmation,
            submitted_at=now,
            confirmed_at=now if confirmation == TxConfirmationStatus.MINED else None,
            message=message,
            # ExecutionResult.fill_price is constrained to [0,1] (Polymarket YES/NO).
            # Hyperliquid actually fills at BTC mark price (~$80k), but we surface
            # the Polymarket-equivalent fill so the paper portfolio + settlement
            # loop can still produce a direction-quality PnL proxy. The TRUE HL
            # entry price is recorded in ``message``.
            fill_price=float(order.limit_price),
        )

        self.last_attempt = TransactionAttempt(
            market_id=order.market_id,
            execution_mode=self.execution_mode,
            lane="hyperliquid",
            intent_id=intent_id,
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=status,
            tx_hash=str(result.order_id) if result.order_id else None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=now,
            confirmed_at=execution.confirmed_at,
            confirmation_status=confirmation,
            message=message,
        )
        return execution

    # ── time-exit watchdog ─────────────────────────────────────────────

    def _close_expired_positions(self, *, now: datetime | None = None) -> int:
        """Force-close any tracked position older than time_exit_seconds.

        Called from place_order() so on every scheduler tick we sweep the
        previous slot's positions before opening new ones.  Returns the
        number of positions closed.
        """
        if self.time_exit_seconds <= 0:
            return 0
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.time_exit_seconds)
        closed = 0
        with self._lock:
            still_open: list[_OpenPosition] = []
            expired: list[_OpenPosition] = []
            for pos in self._open_positions:
                if pos.opened_at <= cutoff:
                    expired.append(pos)
                else:
                    still_open.append(pos)
            self._open_positions = still_open

        for pos in expired:
            try:
                hold_s = (now - pos.opened_at).total_seconds()
                resp = self.client.market_close(size_btc=pos.size_btc)
                if resp.ok:
                    closed += 1
                    logger.info(
                        "hl_time_exit_closed market=%s side=%s size=%.5f hold_s=%.0f hl_status=%s",
                        pos.market_id, "LONG" if pos.is_long else "SHORT",
                        pos.size_btc, hold_s, resp.status,
                    )
                else:
                    logger.warning(
                        "hl_time_exit_close_failed market=%s err=%s",
                        pos.market_id, resp.error,
                    )
            except Exception as exc:
                logger.exception("hl_time_exit_exception market=%s", pos.market_id)
        return closed

    def close_all_open_positions(self) -> int:
        """Force-close every tracked position (called on shutdown)."""
        with self._lock:
            positions = list(self._open_positions)
            self._open_positions = []
        closed = 0
        for pos in positions:
            try:
                resp = self.client.market_close(size_btc=pos.size_btc)
                if resp.ok:
                    closed += 1
            except Exception:
                logger.exception("hl_shutdown_close_exception market=%s", pos.market_id)
        return closed

    @property
    def open_position_count(self) -> int:
        with self._lock:
            return len(self._open_positions)

    # ── helpers ────────────────────────────────────────────────────────

    def _fail(self, order: OrderIntent, intent_id: str, reason: str) -> ExecutionResult:
        now = datetime.now(UTC)
        result = ExecutionResult(
            market_id=order.market_id,
            execution_mode=self.execution_mode,
            status=ExecutionStatus.FAILED,
            side=order.side,
            stake_usd=order.stake_usd,
            order_id=None,
            intent_id=intent_id,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            confirmation_status=TxConfirmationStatus.FAILED,
            submitted_at=now,
            message=reason,
        )
        self.last_attempt = TransactionAttempt(
            market_id=order.market_id,
            execution_mode=self.execution_mode,
            lane="hyperliquid",
            intent_id=intent_id,
            venue=order.venue,
            side=order.side,
            stake_usd=order.stake_usd,
            limit_price=order.limit_price,
            status=result.status,
            tx_hash=None,
            run_id=order.run_id,
            review_queue_id=order.review_queue_id,
            submitted_at=now,
            confirmation_status=TxConfirmationStatus.FAILED,
            message=reason,
        )
        logger.warning("hyperliquid_executor_failed reason=%s order=%s", reason, order)
        return result


def build_default_client_for_mode(
    mode: ExecutionMode,
    *,
    private_key_env: str = "HL_PRIVATE_KEY",
) -> HyperliquidClient:
    """Build a HyperliquidClient with sensible defaults for the given mode."""
    if mode == ExecutionMode.HYPERLIQUID_DRY_RUN:
        cfg = HyperliquidConfig(network="testnet", dry_run=True, private_key_env=private_key_env)
    elif mode == ExecutionMode.HYPERLIQUID_TESTNET:
        cfg = HyperliquidConfig(network="testnet", dry_run=False, private_key_env=private_key_env)
    elif mode == ExecutionMode.HYPERLIQUID_LIVE:
        cfg = HyperliquidConfig(network="mainnet", dry_run=False, private_key_env=private_key_env)
    else:
        raise ValueError(f"unsupported_mode:{mode}")
    return HyperliquidClient(cfg)
