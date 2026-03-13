from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from prediction_market_bot.domain.models import (
    ExecutionResult,
    MarketSnapshot,
    OrderIntent,
    ResearchFinding,
)


@runtime_checkable
class MarketDataPort(Protocol):
    def list_active_markets(self) -> Sequence[MarketSnapshot]:
        ...


@runtime_checkable
class ResearchDataPort(Protocol):
    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        ...


@runtime_checkable
class ExecutionPort(Protocol):
    def place_order(self, order: OrderIntent) -> ExecutionResult:
        ...


@runtime_checkable
class PersistencePort(Protocol):
    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        ...

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        ...


# Backward-compatible aliases.
MarketDataProvider = MarketDataPort
ResearchSource = ResearchDataPort
TradeExecutor = ExecutionPort
