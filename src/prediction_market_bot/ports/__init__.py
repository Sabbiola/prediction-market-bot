"""Backward-compatible exports for legacy internal paths."""

from prediction_market_bot.interfaces.ports import (
    ExecutionPort,
    MarketDataPort,
    MarketDataProvider,
    PersistencePort,
    ResearchDataPort,
    ResearchSource,
    TradeExecutor,
)

__all__ = [
    "ExecutionPort",
    "MarketDataPort",
    "MarketDataProvider",
    "PersistencePort",
    "ResearchDataPort",
    "ResearchSource",
    "TradeExecutor",
]
