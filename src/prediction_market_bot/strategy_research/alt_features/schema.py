from __future__ import annotations

from .models import AltFeatureColumn, AltFeatureSchema

ALT_FEATURE_SCHEMA_VERSION = "alt-v1"

ALT_FEATURE_SCHEMA = AltFeatureSchema(
    version=ALT_FEATURE_SCHEMA_VERSION,
    columns=(
        AltFeatureColumn("row_id", "string", "meta", "Deterministic row identifier: market_id@decision_timestamp_utc."),
        AltFeatureColumn("feature_schema_version", "string", "meta", "Alt-data feature schema contract version."),
        AltFeatureColumn("market_id", "string", "meta", "Market identifier."),
        AltFeatureColumn("event_id", "string", "meta", "Event identifier."),
        AltFeatureColumn("category", "string", "meta", "Market category."),
        AltFeatureColumn("decision_timestamp_utc", "datetime", "meta", "Decision timestamp used for anti-leakage filtering."),
        AltFeatureColumn("label_yes", "int", "target", "Resolved YES label for offline training (1=YES, 0=NO)."),
        AltFeatureColumn("data_quality_flags", "list[string]", "meta", "Flags describing missing sources, fallbacks, or low coverage."),
        AltFeatureColumn(
            "f_alt_evidence_count",
            "int",
            "coverage",
            "Count of linked alt-data evidence points up to decision timestamp.",
        ),
        AltFeatureColumn(
            "f_alt_linked_evidence_count",
            "int",
            "coverage",
            "Count of evidence records in linkage state=linked.",
        ),
        AltFeatureColumn(
            "f_alt_market_linked_coverage",
            "float",
            "coverage",
            "Share of linked evidence over total linked+ambiguous evidence for the market decision.",
        ),
        AltFeatureColumn(
            "f_alt_source_diversity",
            "float",
            "diversity",
            "Normalized source diversity across source identities.",
        ),
        AltFeatureColumn(
            "f_alt_source_credibility_prior",
            "float",
            "credibility",
            "Average deterministic source credibility prior across evidence points.",
        ),
        AltFeatureColumn(
            "f_alt_freshness_decay",
            "float",
            "freshness",
            "Recency decay score computed from evidence age at decision timestamp.",
        ),
        AltFeatureColumn(
            "f_alt_news_volume_24h",
            "int",
            "news",
            "Count of news evidence points in the last 24h before decision timestamp.",
        ),
        AltFeatureColumn(
            "f_alt_news_burstiness_24h",
            "float",
            "news",
            "Relative burstiness of recent news volume vs previous 24h window.",
        ),
        AltFeatureColumn(
            "f_alt_reddit_mentions_24h",
            "int",
            "reddit",
            "Count of reddit evidence points in the last 24h before decision timestamp.",
        ),
        AltFeatureColumn(
            "f_alt_reddit_attention",
            "float",
            "reddit",
            "Log-scaled reddit attention aggregate (score/comments) in the last 24h window.",
        ),
        AltFeatureColumn(
            "f_alt_x_mentions_24h",
            "int",
            "x",
            "Count of X evidence points in the last 24h before decision timestamp.",
        ),
        AltFeatureColumn(
            "f_alt_x_attention",
            "float",
            "x",
            "Log-scaled X attention aggregate from public metrics in the last 24h window.",
        ),
        AltFeatureColumn(
            "f_alt_contradiction_score",
            "float",
            "llm_enrichment",
            "Weighted contradiction score from enrichment outputs.",
        ),
        AltFeatureColumn(
            "f_alt_novelty_score",
            "float",
            "llm_enrichment",
            "Weighted novelty score from enrichment outputs.",
        ),
        AltFeatureColumn(
            "f_alt_catalyst_strength_score",
            "float",
            "llm_enrichment",
            "Weighted catalyst strength score mapped from enrichment catalyst_class.",
        ),
        AltFeatureColumn(
            "f_alt_enrichment_coverage",
            "float",
            "llm_enrichment",
            "Share of evidence points with usable enrichment output.",
        ),
    ),
)
