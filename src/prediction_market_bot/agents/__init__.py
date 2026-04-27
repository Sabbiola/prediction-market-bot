"""Agent implementations and contracts."""

# Protocol interfaces (for type annotations and isinstance checks)
from .contracts import (
    ExecutionAgent as ExecutionAgentProtocol,
    PostmortemAgent as PostmortemAgentProtocol,
    PredictionAgent as PredictionAgentProtocol,
    ResearchAgent as ResearchAgentProtocol,
    RiskAgent as RiskAgentProtocol,
    ScanAgent as ScanAgentProtocol,
    SettlementAgent as SettlementAgentProtocol,
)

# Concrete implementations
from .execution import ExecutionAgent
from .postmortem import PostmortemAgent
from .prediction import PredictionAgent
from .research import ResearchAgent
from .risk import RiskAgent
from .scanner import ScanAgent
from .settlement import SettlementAgent

__all__ = [
    # Concrete implementations
    "ExecutionAgent",
    "PostmortemAgent",
    "PredictionAgent",
    "ResearchAgent",
    "RiskAgent",
    "ScanAgent",
    "SettlementAgent",
    # Protocol interfaces
    "ExecutionAgentProtocol",
    "PostmortemAgentProtocol",
    "PredictionAgentProtocol",
    "ResearchAgentProtocol",
    "RiskAgentProtocol",
    "ScanAgentProtocol",
    "SettlementAgentProtocol",
]
