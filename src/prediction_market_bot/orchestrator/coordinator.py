"""Backward-compatible wrapper for the renamed orchestration package."""

from prediction_market_bot.orchestration.coordinator import PipelineCoordinator, PipelineRecord, PipelineSummary

__all__ = ["PipelineCoordinator", "PipelineRecord", "PipelineSummary"]
