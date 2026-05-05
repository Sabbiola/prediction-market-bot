"""Thin wrapper over the official Hyperliquid Python SDK.

Goals:
- Encapsulate testnet/mainnet selection + wallet construction.
- Expose ONLY the operations Bot E needs:
    * read account state, mark/oracle/funding for BTC perp
    * read L2 order book (for slippage estimation)
    * place market open / market close
    * place a parent + TP + SL atomically (normalTpsl grouping)
    * cancel an order
    * read open orders / fills
- Dry-run mode that returns shaped responses without touching the network,
  so the trading pipeline can be developed and tested without funded wallets.

The official SDK (`hyperliquid-python-sdk`) is the source of truth for
signatures and request schemas.  We wrap it so the rest of the bot only
sees a small, typed surface and we can swap to mainnet by flipping a
single config flag.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HyperliquidConfig:
    network: str = "testnet"           # "testnet" or "mainnet"
    private_key_env: str = "HL_PRIVATE_KEY"
    account_address_env: str = "HL_ACCOUNT_ADDRESS"  # optional; defaults to derived
    coin: str = "BTC"                  # asset symbol (perp)
    dry_run: bool = True               # if True, no orders are sent
    default_slippage: float = 0.005    # 50 bps default slippage on market orders
    min_order_usd: float = 10.0        # Hyperliquid minimum notional


@dataclass(frozen=True)
class MarketSnapshot:
    coin: str
    mark_px: float
    oracle_px: float
    funding_rate_per_hour: float
    open_interest: float
    day_volume_usd: float
    bid_px: float | None
    ask_px: float | None
    bid_sz: float | None
    ask_sz: float | None


@dataclass(frozen=True)
class AccountSnapshot:
    address: str
    account_value_usd: float
    withdrawable_usd: float
    total_notional_position_usd: float
    open_positions: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


@dataclass
class OrderResult:
    """Shape returned to the executor regardless of dry/live mode."""
    ok: bool
    order_id: int | None
    status: str            # "filled" | "resting" | "rejected" | "dry_run"
    avg_fill_px: float | None = None
    filled_sz: float | None = None
    raw: Mapping[str, Any] | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# Real client (only imported lazily so dry-run + tests don't need the SDK)
# ---------------------------------------------------------------------------

class HyperliquidClient:
    def __init__(self, config: HyperliquidConfig, *, env: Mapping[str, str] | None = None) -> None:
        self.config = config
        self._env = dict(env) if env is not None else {}
        self._info = None
        self._exchange = None
        self._wallet = None
        self._account_address = ""

    # ── lazy-init helpers ───────────────────────────────────────────────

    def _ensure_info(self):
        if self._info is not None:
            return self._info
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        url = constants.MAINNET_API_URL if self.config.network == "mainnet" else constants.TESTNET_API_URL
        self._info = Info(url, skip_ws=True)
        return self._info

    def _ensure_exchange(self):
        if self._exchange is not None:
            return self._exchange
        if self.config.dry_run:
            raise RuntimeError("exchange_client_disabled_in_dry_run")
        import os
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        import eth_account

        priv = (self._env.get(self.config.private_key_env)
                or os.environ.get(self.config.private_key_env, "")).strip()
        if not priv:
            raise RuntimeError(f"missing_env_var:{self.config.private_key_env}")
        if not priv.startswith("0x"):
            priv = "0x" + priv
        wallet = eth_account.Account.from_key(priv)
        self._wallet = wallet
        addr_override = (self._env.get(self.config.account_address_env)
                         or os.environ.get(self.config.account_address_env, "")).strip()
        self._account_address = addr_override or wallet.address
        url = constants.MAINNET_API_URL if self.config.network == "mainnet" else constants.TESTNET_API_URL
        self._exchange = Exchange(wallet, url, account_address=self._account_address)
        return self._exchange

    def account_address(self) -> str:
        if self._account_address:
            return self._account_address
        # In dry-run we still want to derive it for read-only queries
        import os
        addr_override = (self._env.get(self.config.account_address_env)
                         or os.environ.get(self.config.account_address_env, "")).strip()
        if addr_override:
            self._account_address = addr_override
            return addr_override
        priv = (self._env.get(self.config.private_key_env)
                or os.environ.get(self.config.private_key_env, "")).strip()
        if not priv:
            return ""
        if not priv.startswith("0x"):
            priv = "0x" + priv
        import eth_account
        self._account_address = eth_account.Account.from_key(priv).address
        return self._account_address

    # ── read paths ──────────────────────────────────────────────────────

    def fetch_market_snapshot(self) -> MarketSnapshot | None:
        try:
            info = self._ensure_info()
            # universe + ctx
            meta_ctx = info.meta_and_asset_ctxs()
            if not (isinstance(meta_ctx, list) and len(meta_ctx) == 2):
                return None
            universe = meta_ctx[0].get("universe", [])
            ctxs = meta_ctx[1]
            try:
                idx = next(i for i, a in enumerate(universe) if a.get("name") == self.config.coin)
            except StopIteration:
                return None
            ctx = ctxs[idx] if idx < len(ctxs) else {}
            # L2 top of book
            book = info.l2_snapshot(self.config.coin)
            levels = book.get("levels", [[], []])
            bids = levels[0]
            asks = levels[1]
            bid = bids[0] if bids else None
            ask = asks[0] if asks else None

            return MarketSnapshot(
                coin=self.config.coin,
                mark_px=float(ctx.get("markPx") or 0.0),
                oracle_px=float(ctx.get("oraclePx") or 0.0),
                funding_rate_per_hour=float(ctx.get("funding") or 0.0),
                open_interest=float(ctx.get("openInterest") or 0.0),
                day_volume_usd=float(ctx.get("dayNtlVlm") or 0.0),
                bid_px=float(bid["px"]) if bid else None,
                ask_px=float(ask["px"]) if ask else None,
                bid_sz=float(bid["sz"]) if bid else None,
                ask_sz=float(ask["sz"]) if ask else None,
            )
        except Exception as exc:
            logger.warning("hl_market_snapshot_failed err=%s", exc)
            return None

    def fetch_account_snapshot(self) -> AccountSnapshot | None:
        addr = self.account_address()
        if not addr:
            return None
        try:
            info = self._ensure_info()
            state = info.user_state(addr) or {}
            margin = state.get("marginSummary", {}) or {}
            return AccountSnapshot(
                address=addr,
                account_value_usd=float(margin.get("accountValue") or 0.0),
                withdrawable_usd=float(state.get("withdrawable") or 0.0),
                total_notional_position_usd=float(margin.get("totalNtlPos") or 0.0),
                open_positions=tuple(state.get("assetPositions") or ()),
            )
        except Exception as exc:
            logger.warning("hl_account_snapshot_failed err=%s", exc)
            return None

    # ── trading paths ───────────────────────────────────────────────────

    def round_size(self, size_btc: float) -> float:
        """BTC perp has szDecimals=5 → step 0.00001."""
        return round(max(0.0, size_btc), 5)

    def market_order_with_tpsl(
        self,
        *,
        is_long: bool,
        size_btc: float,
        leverage: int = 1,
        take_profit_px: float | None = None,
        stop_loss_px: float | None = None,
    ) -> OrderResult:
        """Open a position via market order, optionally bundled with reduce-only TP/SL.

        In dry-run mode this returns a synthesized OrderResult without
        touching the network.
        """
        size_btc = self.round_size(size_btc)
        if size_btc <= 0:
            return OrderResult(False, None, "rejected", error="size_zero")

        if self.config.dry_run:
            logger.info(
                "hl_dry_run_order coin=%s side=%s size=%.5f tp=%s sl=%s leverage=%dx",
                self.config.coin, "LONG" if is_long else "SHORT", size_btc,
                take_profit_px, stop_loss_px, leverage,
            )
            return OrderResult(
                ok=True,
                order_id=None,
                status="dry_run",
                avg_fill_px=None,
                filled_sz=size_btc,
                raw={
                    "dry_run": True,
                    "coin": self.config.coin,
                    "is_long": is_long,
                    "size": size_btc,
                    "leverage": leverage,
                    "tp": take_profit_px,
                    "sl": stop_loss_px,
                },
            )

        try:
            exchange = self._ensure_exchange()
            # Configure leverage first (cross by default).  Hyperliquid SDK
            # exposes `update_leverage(leverage, coin, is_cross=True)`.
            try:
                exchange.update_leverage(leverage, self.config.coin)
            except Exception as exc:
                logger.warning("hl_update_leverage_failed err=%s", exc)

            if take_profit_px is None and stop_loss_px is None:
                # Pure market open (SDK helper applies default slippage cap).
                resp = exchange.market_open(
                    self.config.coin, is_long, size_btc,
                    None, self.config.default_slippage,
                )
                return _parse_response(resp)

            # Atomic parent + TP + SL via bulk_orders normalTpsl grouping.
            close_is_buy = not is_long
            orders: list[Mapping[str, Any]] = []
            # Parent: market open — represented as an aggressive limit
            mkt = self.fetch_market_snapshot()
            ref_px = (mkt.ask_px if is_long else mkt.bid_px) if mkt else None
            if ref_px is None:
                return OrderResult(False, None, "rejected", error="no_ref_price")
            slip_px = ref_px * (1 + self.config.default_slippage if is_long else 1 - self.config.default_slippage)
            orders.append({
                "coin": self.config.coin,
                "is_buy": is_long,
                "sz": size_btc,
                "limit_px": round(slip_px, 1),
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
            })
            if take_profit_px is not None:
                orders.append({
                    "coin": self.config.coin,
                    "is_buy": close_is_buy,
                    "sz": size_btc,
                    "limit_px": round(take_profit_px, 1),
                    "order_type": {"trigger": {"triggerPx": round(take_profit_px, 1), "isMarket": True, "tpsl": "tp"}},
                    "reduce_only": True,
                })
            if stop_loss_px is not None:
                orders.append({
                    "coin": self.config.coin,
                    "is_buy": close_is_buy,
                    "sz": size_btc,
                    "limit_px": round(stop_loss_px, 1),
                    "order_type": {"trigger": {"triggerPx": round(stop_loss_px, 1), "isMarket": True, "tpsl": "sl"}},
                    "reduce_only": True,
                })
            resp = exchange.bulk_orders(orders, grouping="normalTpsl")
            return _parse_response(resp)

        except Exception as exc:
            logger.exception("hl_order_failed")
            return OrderResult(False, None, "rejected", error=f"exception:{exc}")

    def market_close(self, *, size_btc: float | None = None) -> OrderResult:
        """Close (fully or partially) the existing BTC position via market."""
        if self.config.dry_run:
            logger.info("hl_dry_run_close coin=%s size=%s", self.config.coin, size_btc)
            return OrderResult(True, None, "dry_run", filled_sz=size_btc, raw={"dry_run": True, "close": True})
        try:
            exchange = self._ensure_exchange()
            resp = exchange.market_close(self.config.coin, sz=size_btc, slippage=self.config.default_slippage)
            return _parse_response(resp)
        except Exception as exc:
            logger.exception("hl_close_failed")
            return OrderResult(False, None, "rejected", error=f"exception:{exc}")

    def cancel(self, oid: int) -> bool:
        if self.config.dry_run:
            return True
        try:
            exchange = self._ensure_exchange()
            resp = exchange.cancel(self.config.coin, oid)
            return bool(resp and (resp.get("status") == "ok" or resp.get("response", {}).get("type") == "cancel"))
        except Exception as exc:
            logger.warning("hl_cancel_failed err=%s", exc)
            return False


def _parse_response(resp: Mapping[str, Any] | None) -> OrderResult:
    """Best-effort parsing of an Exchange.order / bulk_orders response."""
    if not resp or not isinstance(resp, Mapping):
        return OrderResult(False, None, "rejected", error="no_response", raw=resp)
    if resp.get("status") != "ok":
        return OrderResult(False, None, "rejected", error=str(resp.get("status")), raw=resp)
    data = resp.get("response", {}).get("data", {}) or {}
    statuses = data.get("statuses") or []
    if not statuses:
        return OrderResult(True, None, "ok", raw=resp)
    first = statuses[0] if isinstance(statuses[0], Mapping) else {}
    if "filled" in first:
        f = first["filled"]
        return OrderResult(
            ok=True, order_id=int(f.get("oid") or 0), status="filled",
            avg_fill_px=float(f.get("avgPx")) if f.get("avgPx") else None,
            filled_sz=float(f.get("totalSz")) if f.get("totalSz") else None,
            raw=resp,
        )
    if "resting" in first:
        return OrderResult(
            ok=True, order_id=int(first["resting"].get("oid") or 0),
            status="resting", raw=resp,
        )
    if "error" in first:
        return OrderResult(False, None, "rejected", error=str(first["error"]), raw=resp)
    return OrderResult(True, None, "ok", raw=resp)


# ---------------------------------------------------------------------------
# Convenience: load wallet from a json file (for local dev/tests only)
# ---------------------------------------------------------------------------

def load_wallet_from_file(path: Path) -> tuple[str, str]:
    """Returns (address, private_key). Fails loudly if missing."""
    payload = json.loads(Path(path).read_text())
    return payload["address"], payload["private_key"]
