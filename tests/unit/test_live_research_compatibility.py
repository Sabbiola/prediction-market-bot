from __future__ import annotations

from prediction_market_bot.infrastructure import live_research
from prediction_market_bot.infrastructure import research


def test_live_research_shim_reexports_research_package() -> None:
    assert live_research.LiveResearchIngestionPipeline is research.LiveResearchIngestionPipeline
    assert live_research.StructuredHttpResearchSource is research.StructuredHttpResearchSource
    assert live_research.WikipediaSearchResearchSource is research.WikipediaSearchResearchSource
    assert live_research.OpenAlexWorksResearchSource is research.OpenAlexWorksResearchSource
    assert live_research.build_live_research_sources is research.build_live_research_sources
