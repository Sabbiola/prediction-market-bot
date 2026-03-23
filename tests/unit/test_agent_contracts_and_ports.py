from __future__ import annotations

from typing import Any, Mapping

from prediction_market_bot.agents.contracts import (
    ExecutionAgent as ExecutionAgentContract,
    PostmortemAgent as PostmortemAgentContract,
    PredictionAgent as PredictionAgentContract,
    ResearchAgent as ResearchAgentContract,
    RiskAgent as RiskAgentContract,
    ScanAgent as ScanAgentContract,
    SettlementAgent as SettlementAgentContract,
)
from prediction_market_bot.agents.execution import DryRunExecutor, ExecutionAgent
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings, ScanSettings
from prediction_market_bot.infrastructure import StaticMarketDataProvider, build_default_research_sources
from prediction_market_bot.interfaces import ExecutionPort, MarketDataPort, PersistencePort, ResearchDataPort


class InMemoryPersistence:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, Mapping[str, Any]]] = []
        self.artifacts: list[tuple[str, str, Mapping[str, Any]]] = []

    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        self.events.append((run_id, event_type, payload))

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        self.artifacts.append((run_id, artifact_type, payload))


def test_agent_contracts_are_satisfied() -> None:
    scan = ScanAgent(ScanSettings())
    research = ResearchAgent(build_default_research_sources())
    prediction = PredictionAgent(PredictionSettings())
    risk = RiskAgent(RiskSettings(), PredictionSettings())
    execution = ExecutionAgent("polymarket", DryRunExecutor())
    settlement = SettlementAgent()
    postmortem = PostmortemAgent()

    assert isinstance(scan, ScanAgentContract)
    assert isinstance(research, ResearchAgentContract)
    assert isinstance(prediction, PredictionAgentContract)
    assert isinstance(risk, RiskAgentContract)
    assert isinstance(execution, ExecutionAgentContract)
    assert isinstance(settlement, SettlementAgentContract)
    assert isinstance(postmortem, PostmortemAgentContract)


def test_ports_are_satisfied() -> None:
    provider = StaticMarketDataProvider("polymarket")
    sources = build_default_research_sources()
    executor = DryRunExecutor()
    persistence = InMemoryPersistence()

    assert isinstance(provider, MarketDataPort)
    assert all(isinstance(source, ResearchDataPort) for source in sources)
    assert isinstance(executor, ExecutionPort)
    assert isinstance(persistence, PersistencePort)
