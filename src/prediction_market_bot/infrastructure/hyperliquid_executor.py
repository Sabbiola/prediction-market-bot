"""HyperliquidExecutor — translates a YES/NO OrderIntent into a BTC perp
long/short order on Hyperliquid via the SDK wrapper.

Mapping rules:
- side YES (= "BTC will close higher this slot")  -> LONG  BTC perp
- side NO  (= "BTC will close lower this slot")   -> SHORT BTC perp
- stake_usd at leverage L gives  size_btc = (stake_usd * L) / mark_px
- Take-profit / stop-loss are placed atomically with the parent order so
  the position is bounded even if the bot or the network is unreachable
  for the rest of the 15-minute slot.
- A time-based force-close + HL state reconciliation runs in a background
  thread (every 60s) and at the start of every place_order.  The watchdog:
    1. closes any tracked position older than time_exit_seconds
    2. closes any HL position not in our tracking list (orphan from a
       previous bot run or out-of-band trade) so the in-memory state can
       never silently drift from the exchange
    3. cancels leftover reduce-only TP/SL orders once no position remains
  This aligns the HL position window with the 15-minute prediction horizon
  AND survives bot restarts, which the prior in-memory-only watchdog did not.
- On boot, any existing HL position for this asset is "adopted" with a
  fresh opened_at timestamp so a quick restart doesn't immediately
  market-close a position the bot legitimately just opened.

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
        self._stop_reconcile = threading.Event()
        self._reconcile_thread: threading.Thread | None = None

        # On live testnet/mainnet we must guard against the in-memory tracking
        # list getting wiped on every restart.  At boot we adopt any HL position
        # already on the exchange (giving it a fresh time-exit window so the
        # bot doesn't immediately churn it on restart) and start a 60s
        # reconciliation loop that:
        #   - market-closes any tracked position older than time_exit_seconds
        #   - market-closes any HL position not in the tracking list (orphan
        #     from a previous run)
        #   - cancels reduce-only TP/SL orders left behind once the position
        #     is gone (otherwise they accumulate forever)
        if execution_mode != ExecutionMode.HYPERLIQUID_DRY_RUN:
            self._adopt_existing_positions()
            self._start_reconcile_thread()

    # ── interface required by ExecutionAgent ────────────────────────────

    def place_order(self, order: OrderIntent) -> ExecutionResult:
        intent_id = _intent_id(order)
        now = datetime.now(UTC)

        # Reconcile with the live HL state before opening anything new:
        # closes expired/orphan positions and cancels stale TP/SL orders
        # so we never accumulate state across restarts or skipped cycles.
        self._reconcile_with_hl(now=now)

        # Setup B: when an existing position already agrees with this signal,
        # skip — the original entry's TP/SL/time_exit are still doing their
        # job and pyramiding $10 every 15 minutes inside a 4h hold burns the
        # extra round-trip fee for marginal extra exposure.
        is_long = order.side == OutcomeSide.YES
        with self._lock:
            agreeing = [
                p for p in self._open_positions
                if p.is_long == is_long and p.size_btc > 0
            ]
        if agreeing:
            existing_size = sum(p.size_btc for p in agreeing)
            return self._fail(
                order, intent_id,
                f"hl_skip_position_already_agrees side={'LONG' if is_long else 'SHORT'} "
                f"existing_size_btc={existing_size:.5f} (Setup B: no pyramiding)",
            )

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

    # ── HL state reconciliation ────────────────────────────────────────
    #
    # The bot may be restarted any time (deploy, crash, host reboot).  The
    # in-memory tracking list is therefore unreliable on its own.  We have to
    # talk to HL directly to know what positions and orders actually exist
    # for the wallet, and we close anything that is older than the prediction
    # window or has no matching tracking entry.

    def _adopt_existing_positions(self) -> None:
        """At boot, register every existing HL position in tracking.

        We don't know how old they really are, so we give them a fresh
        time-exit window — the next tick of the reconcile loop will close
        them once they age past time_exit_seconds.  This avoids immediately
        churning a position the user/bot opened seconds before a restart.
        """
        try:
            snap = self.client.fetch_account_snapshot()
        except Exception:
            logger.exception("hl_adopt_failed")
            return
        if snap is None:
            return
        now = datetime.now(UTC)
        adopted = 0
        with self._lock:
            for ap in snap.open_positions:
                pos = ap.get("position", {}) if isinstance(ap, dict) else {}
                if pos.get("coin") != self.client.config.coin:
                    continue
                try:
                    szi = float(pos.get("szi") or 0)
                except (TypeError, ValueError):
                    continue
                if abs(szi) < 1e-9:
                    continue
                self._open_positions.append(_OpenPosition(
                    intent_id="adopted",
                    market_id="adopted",
                    is_long=szi > 0,
                    size_btc=abs(szi),
                    entry_px=float(pos.get("entryPx") or 0.0),
                    opened_at=now,
                    leverage=int((pos.get("leverage") or {}).get("value") or self.leverage),
                ))
                adopted += 1
        if adopted:
            logger.info("hl_adopted_existing_positions count=%d", adopted)

    def _start_reconcile_thread(self) -> None:
        if self._reconcile_thread is not None:
            return
        def _loop() -> None:
            # First tick after a short delay so adoption is processed first.
            self._stop_reconcile.wait(15)
            while not self._stop_reconcile.is_set():
                try:
                    self._reconcile_with_hl()
                except Exception:
                    logger.exception("hl_reconcile_loop_exception")
                self._stop_reconcile.wait(60)
        t = threading.Thread(target=_loop, daemon=True, name="hl-reconcile")
        t.start()
        self._reconcile_thread = t

    def _cancel_orphan_orders(self) -> int:
        """Cancel reduce-only TP/SL orders for our coin.

        Called when no position remains so leftover triggers don't pile up.
        """
        cancelled = 0
        try:
            from hyperliquid.info import Info
            from hyperliquid.utils import constants
            url = (
                constants.MAINNET_API_URL
                if self.client.config.network == "mainnet"
                else constants.TESTNET_API_URL
            )
            info = Info(url, skip_ws=True)
            addr = self.client.account_address()
            if not addr:
                return 0
            orders = info.open_orders(addr) or []
            for o in orders:
                if o.get("coin") != self.client.config.coin:
                    continue
                try:
                    if self.client.cancel(int(o["oid"])):
                        cancelled += 1
                except Exception:
                    logger.debug("hl_cancel_orphan_failed oid=%s", o.get("oid"))
        except Exception:
            logger.debug("hl_cancel_orphan_lookup_failed", exc_info=True)
        if cancelled:
            logger.info("hl_cancelled_orphan_orders count=%d", cancelled)
        return cancelled

    def _reconcile_with_hl(self, *, now: datetime | None = None) -> int:
        """Sync tracking ↔ HL and close anything stale.

        Closes:
          - tracked positions older than time_exit_seconds, OR
          - HL positions not in our tracking list (shouldn't happen, but
            handles edge cases like manual user trades or races).
        Then if no position remains, cancels leftover reduce-only TP/SL.
        Returns the number of positions closed.
        """
        if self.time_exit_seconds <= 0:
            return 0
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.time_exit_seconds)

        try:
            snap = self.client.fetch_account_snapshot()
        except Exception:
            logger.exception("hl_reconcile_snapshot_failed")
            return 0
        if snap is None:
            return 0

        # Sum of |szi| currently on HL for our coin
        hl_total_size = 0.0
        for ap in snap.open_positions:
            pos = ap.get("position", {}) if isinstance(ap, dict) else {}
            if pos.get("coin") != self.client.config.coin:
                continue
            try:
                hl_total_size += abs(float(pos.get("szi") or 0))
            except (TypeError, ValueError):
                pass

        # Decide expiry from our tracking list (oldest first)
        with self._lock:
            tracked = sorted(self._open_positions, key=lambda p: p.opened_at)
            expired = [p for p in tracked if p.opened_at <= cutoff]
            still_open = [p for p in tracked if p.opened_at > cutoff]
            tracked_size = sum(p.size_btc for p in tracked)
            self._open_positions = still_open

        closed = 0

        # Case A: HL has *more* size than we track → some untracked legs exist;
        # close everything to clean state, then cancel orphan orders.
        if hl_total_size > tracked_size + 1e-7:
            logger.warning(
                "hl_reconcile_untracked_position hl_size=%.5f tracked_size=%.5f — closing all",
                hl_total_size, tracked_size,
            )
            try:
                resp = self.client.market_close()
                if resp.ok:
                    closed += 1
            except Exception:
                logger.exception("hl_reconcile_full_close_failed")
            # nuke the tracking list entirely since the position is gone
            with self._lock:
                self._open_positions = []
        elif expired:
            # Case B: only expired tracked positions need closing.
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
                        logger.warning("hl_time_exit_close_failed err=%s", resp.error)
                except Exception:
                    logger.exception("hl_time_exit_exception market=%s", pos.market_id)

        # Cancel leftover TP/SL orders if nothing is open anymore.
        if closed > 0 or hl_total_size == 0.0:
            self._cancel_orphan_orders()

        return closed

    def close_all_open_positions(self) -> int:
        """Force-close every position on HL for our coin (used on shutdown)."""
        try:
            resp = self.client.market_close()
            closed = 1 if resp.ok else 0
        except Exception:
            logger.exception("hl_shutdown_close_exception")
            closed = 0
        with self._lock:
            self._open_positions = []
        self._cancel_orphan_orders()
        return closed

    def shutdown(self) -> None:
        """Stop the reconciliation loop (best-effort)."""
        self._stop_reconcile.set()

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
