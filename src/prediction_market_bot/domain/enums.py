from __future__ import annotations

from enum import Enum


class OutcomeSide(str, Enum):
    YES = "YES"
    NO = "NO"


class MarketStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    HALTED = "HALTED"


class SourceType(str, Enum):
    TWITTER = "TWITTER"
    REDDIT = "REDDIT"
    RSS = "RSS"
    OFFICIAL = "OFFICIAL"
    MANUAL = "MANUAL"


class ExecutionStatus(str, Enum):
    SKIPPED = "SKIPPED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


class OutcomeClassification(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    SKIPPED = "SKIPPED"


class PostmortemCause(str, Enum):
    DATA_GAP = "DATA_GAP"
    RESEARCH_NOISE = "RESEARCH_NOISE"
    CALIBRATION_ERROR = "CALIBRATION_ERROR"
    LIQUIDITY_TRAP = "LIQUIDITY_TRAP"
    RISK_OVERSIZING = "RISK_OVERSIZING"
    EXECUTION_SLIPPAGE = "EXECUTION_SLIPPAGE"
    RESOLUTION_MISREAD = "RESOLUTION_MISREAD"


class TradeReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TradeReviewAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NOTE = "NOTE"
