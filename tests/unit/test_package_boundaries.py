from __future__ import annotations

from prediction_market_bot.interfaces import MarketDataPort as CanonicalMarketDataPort
from prediction_market_bot.orchestration import PipelineCoordinator as CanonicalPipelineCoordinator
from prediction_market_bot.orchestrator import PipelineCoordinator as LegacyPipelineCoordinator
from prediction_market_bot.ports import MarketDataPort as LegacyMarketDataPort


def test_orchestrator_wrapper_exports_canonical_symbols() -> None:
    assert LegacyPipelineCoordinator is CanonicalPipelineCoordinator


def test_ports_wrapper_exports_canonical_symbols() -> None:
    assert LegacyMarketDataPort is CanonicalMarketDataPort
