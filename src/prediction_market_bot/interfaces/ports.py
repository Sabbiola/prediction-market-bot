from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from prediction_market_bot.domain.models import (
    MarketSnapshot,
    PendingSettlementRequest,
    ResolutionCheckResult,
    ResearchFinding,
    TxIntent,
    TxReceipt,
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
    def place_order(self, order: TxIntent) -> TxReceipt:
        ...


@runtime_checkable
class PaperExecutorPort(ExecutionPort, Protocol):
    def place_order(self, order: TxIntent) -> TxReceipt:
        ...


@runtime_checkable
class ShadowSignExecutorPort(ExecutionPort, Protocol):
    def place_order(self, order: TxIntent) -> TxReceipt:
        ...


@runtime_checkable
class SandboxChainExecutorPort(ExecutionPort, Protocol):
    def place_order(self, order: TxIntent) -> TxReceipt:
        ...


@runtime_checkable
class PersistencePort(Protocol):
    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        ...

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        ...


@runtime_checkable
class ResolutionPollerPort(Protocol):
    def poll(self, request: PendingSettlementRequest) -> ResolutionCheckResult:
        ...


# Backward-compatible aliases.
MarketDataProvider = MarketDataPort
ResearchSource = ResearchDataPort
TradeExecutor = ExecutionPort
PaperExecutor = PaperExecutorPort
ShadowSignExecutor = ShadowSignExecutorPort
SandboxChainExecutor = SandboxChainExecutorPort
