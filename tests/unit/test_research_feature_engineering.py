from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.services.research_features import (
    ResearchEvidencePoint,
    build_research_feature_bundle,
)


def _evidence(
    *,
    summary: str,
    sentiment: float,
    credibility: float,
    source_name: str,
    source_type: SourceType,
    published_at: datetime,
    query: str = "",
) -> ResearchEvidencePoint:
    return ResearchEvidencePoint(
        summary=summary,
        sentiment=sentiment,
        credibility=credibility,
        source_name=source_name,
        source_type=source_type,
        published_at_utc=published_at,
        query=query,
    )


def test_research_feature_bundle_market_relevance_changes_with_content() -> None:
    as_of = datetime(2026, 3, 21, 12, 0, tzinfo=UTC)
    aligned = _evidence(
        summary="Inflation decline in euro area expected by economists.",
        sentiment=0.2,
        credibility=0.8,
        source_name="openalex-works",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=2),
        query="inflation decline euro area",
    )
    unrelated = _evidence(
        summary="Sports championship roster updates and transfer rumors.",
        sentiment=0.2,
        credibility=0.8,
        source_name="openalex-works",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=2),
        query="inflation decline euro area",
    )

    aligned_bundle = build_research_feature_bundle(
        market_title="Will inflation decline in euro area?",
        market_category="macro",
        event_context="eurozone inflation event",
        decision_timestamp_utc=as_of,
        evidence_points=(aligned,),
    )
    unrelated_bundle = build_research_feature_bundle(
        market_title="Will inflation decline in euro area?",
        market_category="macro",
        event_context="eurozone inflation event",
        decision_timestamp_utc=as_of,
        evidence_points=(unrelated,),
    )

    assert aligned_bundle.market_relevance_score > unrelated_bundle.market_relevance_score
    assert aligned_bundle.entity_event_alignment_score > unrelated_bundle.entity_event_alignment_score


def test_research_feature_bundle_timeliness_decay_prefers_fresh_evidence() -> None:
    as_of = datetime(2026, 3, 21, 12, 0, tzinfo=UTC)
    fresh = _evidence(
        summary="Fresh macro release signals declining inflation.",
        sentiment=0.3,
        credibility=0.75,
        source_name="wikipedia-search",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=1),
    )
    stale = _evidence(
        summary="Old macro release signals declining inflation.",
        sentiment=0.3,
        credibility=0.75,
        source_name="wikipedia-search",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(days=21),
    )

    fresh_bundle = build_research_feature_bundle(
        market_title="Will inflation decline?",
        market_category="macro",
        event_context="inflation event",
        decision_timestamp_utc=as_of,
        evidence_points=(fresh,),
    )
    stale_bundle = build_research_feature_bundle(
        market_title="Will inflation decline?",
        market_category="macro",
        event_context="inflation event",
        decision_timestamp_utc=as_of,
        evidence_points=(stale,),
    )

    assert fresh_bundle.timeliness_decay > stale_bundle.timeliness_decay
    assert fresh_bundle.freshness_hours < stale_bundle.freshness_hours


def test_research_feature_bundle_contradiction_score_detects_conflict() -> None:
    as_of = datetime(2026, 3, 21, 12, 0, tzinfo=UTC)
    bullish = _evidence(
        summary="Policy approval probability is increasing rapidly.",
        sentiment=0.8,
        credibility=0.9,
        source_name="source-a",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=4),
    )
    bearish = _evidence(
        summary="Policy approval likely to fail according to surveys.",
        sentiment=-0.8,
        credibility=0.9,
        source_name="source-b",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=5),
    )
    same_side = _evidence(
        summary="Policy approval remains likely based on committee signals.",
        sentiment=0.6,
        credibility=0.9,
        source_name="source-c",
        source_type=SourceType.RSS,
        published_at=as_of - timedelta(hours=6),
    )

    conflicting_bundle = build_research_feature_bundle(
        market_title="Will policy pass?",
        market_category="politics",
        event_context="policy vote",
        decision_timestamp_utc=as_of,
        evidence_points=(bullish, bearish),
    )
    same_direction_bundle = build_research_feature_bundle(
        market_title="Will policy pass?",
        market_category="politics",
        event_context="policy vote",
        decision_timestamp_utc=as_of,
        evidence_points=(bullish, same_side),
    )

    assert conflicting_bundle.contradiction_score > same_direction_bundle.contradiction_score


def test_research_feature_bundle_is_deterministic_under_input_permutation() -> None:
    as_of = datetime(2026, 3, 21, 12, 0, tzinfo=UTC)
    points = (
        _evidence(
            summary="Signal A points to higher chance.",
            sentiment=0.4,
            credibility=0.8,
            source_name="a",
            source_type=SourceType.RSS,
            published_at=as_of - timedelta(hours=3),
        ),
        _evidence(
            summary="Signal B introduces risk and uncertainty.",
            sentiment=-0.2,
            credibility=0.7,
            source_name="b",
            source_type=SourceType.REDDIT,
            published_at=as_of - timedelta(hours=2),
        ),
        _evidence(
            summary="Signal C confirms timing from official release.",
            sentiment=0.3,
            credibility=0.9,
            source_name="c",
            source_type=SourceType.OFFICIAL,
            published_at=as_of - timedelta(hours=1),
        ),
    )
    bundle_a = build_research_feature_bundle(
        market_title="Will event happen soon?",
        market_category="general",
        event_context="event context",
        decision_timestamp_utc=as_of,
        evidence_points=points,
    )
    bundle_b = build_research_feature_bundle(
        market_title="Will event happen soon?",
        market_category="general",
        event_context="event context",
        decision_timestamp_utc=as_of,
        evidence_points=(points[2], points[0], points[1]),
    )

    assert bundle_a.to_runtime_dict() == bundle_b.to_runtime_dict()
    assert bundle_a.to_feature_row_dict() == bundle_b.to_feature_row_dict()
