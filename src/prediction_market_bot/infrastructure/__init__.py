"""Infrastructure adapters (dry-run and mocks)."""

from .http_client import HttpClientError, HttpErrorMetadata, HttpJsonResponse, StructuredHttpClient
from .live_market_data import LiveMarketBatch, PolymarketReadOnlyMarketDataAdapter
from .live_research import (
    LiveResearchIngestionPipeline,
    OpenAlexWorksResearchSource,
    ResearchSourcePayloadError,
    ResearchIngestionBatch,
    StructuredHttpResearchSource,
    WikipediaSearchResearchSource,
    build_live_research_sources,
)
from .mock_sources import StaticMarketDataProvider, StaticResearchSource, build_default_research_sources
from .persistence import JsonlPersistence
from .operational_sqlite import SqliteOperationalRepositories, bootstrap_operational_schema
from .sandbox_chain import SandboxChainExecutor

__all__ = [
    "JsonlPersistence",
    "SqliteOperationalRepositories",
    "bootstrap_operational_schema",
    "StructuredHttpClient",
    "HttpJsonResponse",
    "HttpErrorMetadata",
    "HttpClientError",
    "LiveMarketBatch",
    "PolymarketReadOnlyMarketDataAdapter",
    "LiveResearchIngestionPipeline",
    "ResearchSourcePayloadError",
    "ResearchIngestionBatch",
    "StructuredHttpResearchSource",
    "WikipediaSearchResearchSource",
    "OpenAlexWorksResearchSource",
    "build_live_research_sources",
    "StaticMarketDataProvider",
    "StaticResearchSource",
    "build_default_research_sources",
    "SandboxChainExecutor",
]
