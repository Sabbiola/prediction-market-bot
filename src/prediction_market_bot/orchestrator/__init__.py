"""Deprecated compatibility package.

Canonical imports should target ``prediction_market_bot.orchestration``.
"""

import warnings

warnings.warn(
    "prediction_market_bot.orchestrator is deprecated — "
    "use prediction_market_bot.orchestration instead.",
    DeprecationWarning,
    stacklevel=2,
)

from prediction_market_bot.orchestration import PipelineCoordinator, PipelineRecord, PipelineSummary

__all__ = ["PipelineCoordinator", "PipelineRecord", "PipelineSummary"]
