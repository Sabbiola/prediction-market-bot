"""Infrastructure adapters (dry-run and mocks)."""

from .live_market_data import LiveMarketBatch, PolymarketReadOnlyMarketDataAdapter
from .live_research import (
    LiveResearchIngestionPipeline,
    OpenAlexWorksResearchSource,
    ResearchIngestionBatch,
    StructuredHttpResearchSource,
    WikipediaSearchResearchSource,
    build_live_research_sources,
)
from .mock_sources import StaticMarketDataProvider, StaticResearchSource, build_default_research_sources
from .persistence import JsonlPersistence

__all__ = [
    "JsonlPersistence",
    "LiveMarketBatch",
    "PolymarketReadOnlyMarketDataAdapter",
    "LiveResearchIngestionPipeline",
    "ResearchIngestionBatch",
    "StructuredHttpResearchSource",
    "WikipediaSearchResearchSource",
    "OpenAlexWorksResearchSource",
    "build_live_research_sources",
    "StaticMarketDataProvider",
    "StaticResearchSource",
    "build_default_research_sources",
]
