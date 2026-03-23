"""Offline strategy-research bounded context.

The package intentionally uses lazy symbol loading to avoid importing
heavy runtime modules during simple metadata/contract imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "BackfillSummary": (".data_ingest", "BackfillSummary"),
    "DatasetInspection": (".data_ingest", "DatasetInspection"),
    "DatasetVerification": (".data_ingest", "DatasetVerification"),
    "HistoricalDataIngestionService": (".data_ingest", "HistoricalDataIngestionService"),
    "HistoricalResolvedMarketProvider": (".data_ingest", "HistoricalResolvedMarketProvider"),
    "BenchmarkComparisonSummary": (".benchmarking", "BenchmarkComparisonSummary"),
    "AblationRunSummary": (".benchmarking", "AblationRunSummary"),
    "AltDataVariantComparisonSummary": (".benchmarking", "AltDataVariantComparisonSummary"),
    "BenchmarkRunSummary": (".benchmarking", "BenchmarkRunSummary"),
    "LabelBuildSummary": (".benchmarking", "LabelBuildSummary"),
    "StrategyResearchBenchmarkService": (".benchmarking", "StrategyResearchBenchmarkService"),
    "ResearchAlignmentVerification": (".research_corpus", "ResearchAlignmentVerification"),
    "ResearchCorpusBackfillSummary": (".research_corpus", "ResearchCorpusBackfillSummary"),
    "ResearchCorpusInspection": (".research_corpus", "ResearchCorpusInspection"),
    "ResearchEvidenceArchivalService": (".research_corpus", "ResearchEvidenceArchivalService"),
    "NewsBackfillSummary": (".news_corpus", "NewsBackfillSummary"),
    "NewsCorpusInspection": (".news_corpus", "NewsCorpusInspection"),
    "NewsCorpusVerification": (".news_corpus", "NewsCorpusVerification"),
    "NewsCorpusService": (".news_corpus", "NewsCorpusService"),
    "NewsSourceVerification": (".news_corpus", "NewsSourceVerification"),
    "RedditBackfillSummary": (".reddit_corpus", "RedditBackfillSummary"),
    "RedditCorpusInspection": (".reddit_corpus", "RedditCorpusInspection"),
    "RedditCorpusVerification": (".reddit_corpus", "RedditCorpusVerification"),
    "RedditCorpusService": (".reddit_corpus", "RedditCorpusService"),
    "RedditSourceVerification": (".reddit_corpus", "RedditSourceVerification"),
    "XBackfillSummary": (".x_corpus", "XBackfillSummary"),
    "XCorpusInspection": (".x_corpus", "XCorpusInspection"),
    "XCorpusVerification": (".x_corpus", "XCorpusVerification"),
    "XCorpusService": (".x_corpus", "XCorpusService"),
    "XSourceVerification": (".x_corpus", "XSourceVerification"),
    "EvidenceMarketLinkageService": (".linkage", "EvidenceMarketLinkageService"),
    "LinkageBuildSummary": (".linkage", "LinkageBuildSummary"),
    "LinkageInspection": (".linkage", "LinkageInspection"),
    "LinkageQualityVerification": (".linkage", "LinkageQualityVerification"),
    "AltDataLlmEnrichmentService": (".llm_enrichment", "AltDataLlmEnrichmentService"),
    "LlmEnrichmentSummary": (".llm_enrichment", "LlmEnrichmentSummary"),
    "LlmEnrichmentInspection": (".llm_enrichment", "LlmEnrichmentInspection"),
    "FeatureBuildSummary": (".features", "FeatureBuildSummary"),
    "FeatureDatasetBuilderService": (".features", "FeatureDatasetBuilderService"),
    "FeatureParityVerification": (".features", "FeatureParityVerification"),
    "FeatureSchemaInspection": (".features", "FeatureSchemaInspection"),
    "AltFeatureBuildSummary": (".alt_features", "AltFeatureBuildSummary"),
    "AltFeatureDatasetBuilderService": (".alt_features", "AltFeatureDatasetBuilderService"),
    "AltFeatureParityVerification": (".alt_features", "AltFeatureParityVerification"),
    "AltFeatureSchemaInspection": (".alt_features", "AltFeatureSchemaInspection"),
    "StrategyReportSummary": (".simulator", "StrategyReportSummary"),
    "WalkForwardSimulationSummary": (".simulator", "WalkForwardSimulationSummary"),
    "WalkForwardStrategyService": (".simulator", "WalkForwardStrategyService"),
    "CalibrationRunSummary": (".training", "CalibrationRunSummary"),
    "ModelCardSummary": (".training", "ModelCardSummary"),
    "ModelComparisonSummary": (".training", "ModelComparisonSummary"),
    "TrainingRunSummary": (".training", "TrainingRunSummary"),
    "StrategyTrainingLabService": (".training", "StrategyTrainingLabService"),
}

__all__ = sorted(_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    return getattr(module, attr_name)
