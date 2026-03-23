"""Deprecated compatibility package.

Canonical imports should target ``prediction_market_bot.orchestration``.
"""

from prediction_market_bot.orchestration import PipelineCoordinator, PipelineRecord, PipelineSummary

__all__ = ["PipelineCoordinator", "PipelineRecord", "PipelineSummary"]
