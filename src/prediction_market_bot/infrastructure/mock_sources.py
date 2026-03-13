from __future__ import annotations

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.interfaces import MarketDataProvider, ResearchSource


class StaticMarketDataProvider(MarketDataProvider):
    def __init__(self, venue: str) -> None:
        self.venue = venue

    def list_active_markets(self) -> list[MarketSnapshot]:
        return [
            MarketSnapshot.from_yes_price(
                market_id="demo-1",
                venue=self.venue,
                title="Will candidate X win event Y?",
                yes_price=0.42,
                liquidity_usd=50_000,
                volume_24h_usd=25_000,
                spread_bps=120,
                hours_to_resolution=18,
                last_price_move_bps=220,
                category="politics",
            ),
            MarketSnapshot.from_yes_price(
                market_id="demo-2",
                venue=self.venue,
                title="Will inflation print below 2.5% this quarter?",
                yes_price=0.31,
                liquidity_usd=65_000,
                volume_24h_usd=40_000,
                spread_bps=140,
                hours_to_resolution=72,
                last_price_move_bps=-80,
                category="macro",
            ),
        ]


class StaticResearchSource(ResearchSource):
    def __init__(self, source_type: SourceType, source_name: str, sentiment: float, credibility: float) -> None:
        self.source_type = source_type
        self.source_name = source_name
        self.sentiment = sentiment
        self.credibility = credibility

    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source_type=self.source_type,
                source_name=self.source_name,
                summary=f"Mock research for {market.market_id}",
                sentiment=self.sentiment,
                credibility=self.credibility,
            )
        ]


def build_default_research_sources() -> list[ResearchSource]:
    return [
        StaticResearchSource(SourceType.RSS, "mock-rss", sentiment=0.30, credibility=0.70),
        StaticResearchSource(SourceType.REDDIT, "mock-reddit", sentiment=0.10, credibility=0.55),
        StaticResearchSource(SourceType.OFFICIAL, "mock-official", sentiment=0.20, credibility=0.90),
    ]
