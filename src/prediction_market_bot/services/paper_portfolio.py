from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import OrderIntent
from prediction_market_bot.interfaces import PersistencePort


@dataclass(slots=True, frozen=True)
class PaperPositionSnapshot:
    market_id: str
    side: OutcomeSide
    shares: float
    avg_entry_price: float
    cost_basis_usd: float
    mark_price: float
    market_value_usd: float
    unrealized_pnl_usd: float

    def to_dict(self) -> dict[str, object]:
        return {
            "market_id": self.market_id,
            "side": self.side.value,
            "shares": round(self.shares, 8),
            "avg_entry_price": round(self.avg_entry_price, 6),
            "cost_basis_usd": round(self.cost_basis_usd, 2),
            "mark_price": round(self.mark_price, 6),
            "market_value_usd": round(self.market_value_usd, 2),
            "unrealized_pnl_usd": round(self.unrealized_pnl_usd, 2),
        }


@dataclass(slots=True, frozen=True)
class PaperPortfolioSnapshot:
    as_of: datetime
    position_count: int
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    total_pnl_usd: float
    total_exposure_usd: float
    market_exposure_usd: dict[str, float]
    positions: tuple[PaperPositionSnapshot, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "position_count": self.position_count,
            "realized_pnl_usd": round(self.realized_pnl_usd, 2),
            "unrealized_pnl_usd": round(self.unrealized_pnl_usd, 2),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "total_exposure_usd": round(self.total_exposure_usd, 2),
            "market_exposure_usd": {
                market_id: round(value, 2) for market_id, value in sorted(self.market_exposure_usd.items())
            },
            "positions": [position.to_dict() for position in self.positions],
        }


@dataclass(slots=True)
class _PaperPosition:
    market_id: str
    side: OutcomeSide
    shares: float
    cost_basis_usd: float
    avg_entry_price: float
    mark_price: float

    @property
    def market_value_usd(self) -> float:
        return self.shares * self.mark_price

    @property
    def unrealized_pnl_usd(self) -> float:
        return self.market_value_usd - self.cost_basis_usd

    def apply_fill(self, *, shares: float, filled_stake_usd: float, fill_price: float) -> None:
        total_shares = self.shares + shares
        total_cost = self.cost_basis_usd + filled_stake_usd
        self.shares = total_shares
        self.cost_basis_usd = total_cost
        self.avg_entry_price = total_cost / max(total_shares, 1e-9)
        self.mark_price = fill_price


class PaperPortfolioEngine:
    """In-memory paper portfolio with event persistence and replay support."""

    def __init__(
        self,
        *,
        persistence: PersistencePort | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.persistence = persistence
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._positions: dict[tuple[str, OutcomeSide], _PaperPosition] = {}
        self._realized_pnl_usd = 0.0

    def simulate_order_fill(
        self,
        *,
        run_id: str,
        order: OrderIntent,
        fill_ratio: float = 1.0,
        slippage_bps: int = 0,
    ) -> Mapping[str, object]:
        ratio = self._clamp(fill_ratio, lower=0.0, upper=1.0)
        filled_stake_usd = round(order.stake_usd * ratio, 2)
        fill_price = self._fill_price(order.limit_price, slippage_bps=slippage_bps)
        shares = round(filled_stake_usd / max(fill_price, 1e-9), 8) if filled_stake_usd > 0.0 else 0.0

        payload: dict[str, object] = {
            "event_type": "fill",
            "timestamp": self.now_fn().isoformat(),
            "market_id": order.market_id,
            "side": order.side.value,
            "filled_stake_usd": filled_stake_usd,
            "fill_price": fill_price,
            "shares": shares,
            "limit_price": order.limit_price,
            "fill_ratio": ratio,
            "slippage_bps": slippage_bps,
            "venue": order.venue,
            "order_rationale": order.rationale,
        }
        self._apply_fill_event(payload)
        self._persist_event(run_id, payload)
        return payload

    def mark_market(self, *, run_id: str, market_id: str, yes_price: float) -> Mapping[str, object]:
        clamped_yes = self._clamp(yes_price, lower=0.0, upper=1.0)
        payload: dict[str, object] = {
            "event_type": "mark",
            "timestamp": self.now_fn().isoformat(),
            "market_id": market_id,
            "yes_price": clamped_yes,
        }
        self._apply_mark_event(payload)
        self._persist_event(run_id, payload)
        return payload

    def settle_market(self, *, run_id: str, market_id: str, resolved_yes: bool) -> Mapping[str, object]:
        payload: dict[str, object] = {
            "event_type": "settle",
            "timestamp": self.now_fn().isoformat(),
            "market_id": market_id,
            "resolved_yes": bool(resolved_yes),
        }
        self._apply_settle_event(payload)
        self._persist_event(run_id, payload)
        return payload

    def replay_event(self, payload: Mapping[str, Any]) -> None:
        event_type = payload.get("event_type")
        if event_type == "fill":
            self._apply_fill_event(payload)
        elif event_type == "mark":
            self._apply_mark_event(payload)
        elif event_type == "settle":
            self._apply_settle_event(payload)

    def replay_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, Mapping):
                self.replay_event(payload)

    def snapshot(self) -> PaperPortfolioSnapshot:
        positions = tuple(
            sorted(
                (
                    PaperPositionSnapshot(
                        market_id=position.market_id,
                        side=position.side,
                        shares=position.shares,
                        avg_entry_price=position.avg_entry_price,
                        cost_basis_usd=position.cost_basis_usd,
                        mark_price=position.mark_price,
                        market_value_usd=position.market_value_usd,
                        unrealized_pnl_usd=position.unrealized_pnl_usd,
                    )
                    for position in self._positions.values()
                ),
                key=lambda item: (item.market_id, item.side.value),
            )
        )
        market_exposure: dict[str, float] = {}
        unrealized_total = 0.0
        for position in positions:
            market_exposure[position.market_id] = market_exposure.get(position.market_id, 0.0) + position.market_value_usd
            unrealized_total += position.unrealized_pnl_usd

        total_exposure = sum(market_exposure.values())
        realized = self._realized_pnl_usd
        return PaperPortfolioSnapshot(
            as_of=self.now_fn(),
            position_count=len(positions),
            realized_pnl_usd=round(realized, 2),
            unrealized_pnl_usd=round(unrealized_total, 2),
            total_pnl_usd=round(realized + unrealized_total, 2),
            total_exposure_usd=round(total_exposure, 2),
            market_exposure_usd=market_exposure,
            positions=positions,
        )

    def _apply_fill_event(self, payload: Mapping[str, Any]) -> None:
        market_id = self._as_str(payload.get("market_id"))
        side = self._as_side(payload.get("side"))
        filled_stake_usd = self._as_float(payload.get("filled_stake_usd"))
        fill_price = self._as_float(payload.get("fill_price"))
        shares = self._as_float(payload.get("shares"))
        if market_id is None or side is None:
            return
        if filled_stake_usd <= 0.0 or fill_price <= 0.0 or shares <= 0.0:
            return

        key = (market_id, side)
        if key not in self._positions:
            self._positions[key] = _PaperPosition(
                market_id=market_id,
                side=side,
                shares=shares,
                cost_basis_usd=filled_stake_usd,
                avg_entry_price=filled_stake_usd / max(shares, 1e-9),
                mark_price=fill_price,
            )
            return

        self._positions[key].apply_fill(shares=shares, filled_stake_usd=filled_stake_usd, fill_price=fill_price)

    def _apply_mark_event(self, payload: Mapping[str, Any]) -> None:
        market_id = self._as_str(payload.get("market_id"))
        yes_price = self._as_float(payload.get("yes_price"))
        if market_id is None:
            return
        yes_price = self._clamp(yes_price, lower=0.0, upper=1.0)
        for side in (OutcomeSide.YES, OutcomeSide.NO):
            key = (market_id, side)
            position = self._positions.get(key)
            if not position:
                continue
            mark_price = yes_price if side is OutcomeSide.YES else 1.0 - yes_price
            position.mark_price = mark_price

    def _apply_settle_event(self, payload: Mapping[str, Any]) -> None:
        market_id = self._as_str(payload.get("market_id"))
        resolved_yes = bool(payload.get("resolved_yes", False))
        if market_id is None:
            return

        keys = [key for key in self._positions if key[0] == market_id]
        for key in keys:
            position = self._positions[key]
            won = (position.side is OutcomeSide.YES and resolved_yes) or (
                position.side is OutcomeSide.NO and not resolved_yes
            )
            payout_usd = position.shares if won else 0.0
            realized = payout_usd - position.cost_basis_usd
            self._realized_pnl_usd += realized
            del self._positions[key]

    def _persist_event(self, run_id: str, payload: Mapping[str, object]) -> None:
        if not self.persistence:
            return
        self.persistence.write_artifact(run_id, "paper_portfolio_events", payload)
        self.persistence.write_artifact(run_id, "paper_portfolio_snapshots", self.snapshot().to_dict())
        event_type = self._as_str(payload.get("event_type")) or "unknown"
        self.persistence.write_run_event(run_id, f"paper_portfolio_{event_type}", payload)

    @staticmethod
    def _fill_price(limit_price: float, *, slippage_bps: int) -> float:
        slipped = limit_price + (slippage_bps / 10_000.0)
        return round(min(max(slipped, 0.01), 0.99), 6)

    @staticmethod
    def _as_str(value: Any) -> str | None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        return None

    @staticmethod
    def _as_side(value: Any) -> OutcomeSide | None:
        if isinstance(value, OutcomeSide):
            return value
        if isinstance(value, str):
            text = value.strip().upper()
            if text in {"YES", "NO"}:
                return OutcomeSide(text)
        return None

    @staticmethod
    def _as_float(value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if text:
                try:
                    return float(text)
                except ValueError:
                    return 0.0
        return 0.0

    @staticmethod
    def _clamp(value: float, *, lower: float, upper: float) -> float:
        return min(max(value, lower), upper)
