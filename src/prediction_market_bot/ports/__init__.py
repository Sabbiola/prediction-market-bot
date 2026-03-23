"""Deprecated compatibility package.

Canonical imports should target ``prediction_market_bot.interfaces``.
"""

from prediction_market_bot.interfaces.ports import (
    ExecutionPort,
    MarketDataPort,
    MarketDataProvider,
    PaperExecutor,
    PaperExecutorPort,
    PersistencePort,
    SandboxChainExecutor,
    SandboxChainExecutorPort,
    ShadowSignExecutor,
    ShadowSignExecutorPort,
    ResearchDataPort,
    ResearchSource,
    TradeExecutor,
)

__all__ = [
    "ExecutionPort",
    "MarketDataPort",
    "MarketDataProvider",
    "PaperExecutorPort",
    "PaperExecutor",
    "PersistencePort",
    "ShadowSignExecutorPort",
    "ShadowSignExecutor",
    "SandboxChainExecutorPort",
    "SandboxChainExecutor",
    "ResearchDataPort",
    "ResearchSource",
    "TradeExecutor",
]
