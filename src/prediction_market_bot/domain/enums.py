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


# Shared transaction-plane status enum.
# Kept as an alias to preserve stable behavior across existing execution flows.
TxStatus = ExecutionStatus


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SHADOW_SIGN = "SHADOW_SIGN"
    SANDBOX_CHAIN = "SANDBOX_CHAIN"
    # POLYMARKET_LIVE: submits real signed orders to clob.polymarket.com on
    # Polygon mainnet.  Requires POLYMARKET_PRIVATE_KEY env var and funded wallet.
    # Enable only after passing dress-rehearsal in SANDBOX_CHAIN mode.
    POLYMARKET_LIVE = "POLYMARKET_LIVE"
    LIVE_DISABLED = "LIVE_DISABLED"


class RuntimeMode(str, Enum):
    DRY_RUN_STATIC = "DRY_RUN_STATIC"
    PAPER_LIVE = "PAPER_LIVE"
    SANDBOX_CHAIN = "SANDBOX_CHAIN"
    LIVE_DISABLED = "LIVE_DISABLED"


class ProviderSelection(str, Enum):
    AUTO = "AUTO"
    STATIC = "STATIC"
    LIVE = "LIVE"


class ProviderFailurePolicy(str, Enum):
    FAIL_FAST = "FAIL_FAST"
    FALLBACK_TO_STATIC = "FALLBACK_TO_STATIC"


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
    PENDING_REVIEW = "PENDING_REVIEW"
    # Backward-compatible legacy alias for stored artifacts.
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TradeReviewAction(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NOTE = "NOTE"


class SettlementRequestState(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"


class ResolutionStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class TxConfirmationStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"
    MINED = "MINED"
    DROPPED = "DROPPED"
    REPLACED = "REPLACED"
    FAILED = "FAILED"
