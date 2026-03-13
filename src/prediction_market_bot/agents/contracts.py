from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from prediction_market_bot.domain.models import (
    ExecutionResult,
    MarketCandidate,
    MarketSnapshot,
    OrderIntent,
    PostmortemReport,
    PredictionResult,
    ResearchPacket,
    RiskDecision,
    SettlementResult,
)


@runtime_checkable
class ScanAgent(Protocol):
    name: str

    def run(self, markets: Sequence[MarketSnapshot]) -> list[MarketCandidate]:
        ...


@runtime_checkable
class ResearchAgent(Protocol):
    name: str

    def run(self, candidate: MarketCandidate) -> ResearchPacket:
        ...


@runtime_checkable
class PredictionAgent(Protocol):
    name: str

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        ...


@runtime_checkable
class RiskAgent(Protocol):
    name: str

    def run(
        self,
        prediction: PredictionResult,
        candidate: MarketCandidate | None = None,
        portfolio: object | None = None,
    ) -> RiskDecision:
        ...


@runtime_checkable
class ExecutionAgent(Protocol):
    name: str

    def build_order_intent(self, risk: RiskDecision, prediction: PredictionResult) -> OrderIntent:
        ...

    def run(self, risk: RiskDecision, prediction: PredictionResult) -> ExecutionResult:
        ...


@runtime_checkable
class SettlementAgent(Protocol):
    name: str

    def settle(self, execution: ExecutionResult, resolved_yes: bool) -> SettlementResult:
        ...


@runtime_checkable
class PostmortemAgent(Protocol):
    name: str

    def run(
        self,
        settled_trade: SettlementResult,
        prediction: PredictionResult,
        research: ResearchPacket,
    ) -> PostmortemReport:
        ...
