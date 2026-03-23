from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Sequence

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import ResearchFinding

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
}
_SOURCE_TYPE_PRIORS: dict[SourceType, float] = {
    SourceType.OFFICIAL: 0.90,
    SourceType.RSS: 0.72,
    SourceType.MANUAL: 0.65,
    SourceType.REDDIT: 0.52,
    SourceType.TWITTER: 0.45,
}
_SOURCE_NAME_PRIORS: dict[str, float] = {
    "openalex-works": 0.84,
    "openalex": 0.84,
    "wikipedia-search": 0.78,
    "wikipedia": 0.78,
}


def _clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _parse_datetime_utc(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        if len(text) == 10 and text.count("-") == 2:
            try:
                parsed = datetime.fromisoformat(f"{text}T00:00:00+00:00")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tokenize(text: str) -> set[str]:
    return {token for token in _WORD_RE.findall(text.lower()) if token not in _STOPWORDS}


def _overlap_ratio(reference: set[str], observed: set[str]) -> float:
    if not reference or not observed:
        return 0.0
    return _clamp(len(reference & observed) / len(reference), lower=0.0, upper=1.0)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _source_prior(source_type: SourceType, source_name: str) -> float:
    normalized_name = source_name.strip().lower()
    if normalized_name in _SOURCE_NAME_PRIORS:
        return _SOURCE_NAME_PRIORS[normalized_name]
    return _SOURCE_TYPE_PRIORS.get(source_type, 0.60)


def _parse_provenance(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values:
        text = item.strip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key_text = key.strip().lower()
        value_text = value.strip()
        if not key_text or not value_text:
            continue
        parsed[key_text] = value_text
    return parsed


@dataclass(slots=True, frozen=True)
class ResearchEvidencePoint:
    summary: str
    sentiment: float
    credibility: float
    source_name: str
    source_type: SourceType
    published_at_utc: datetime | None = None
    query: str = ""
    relevance_hint: float | None = None
    provenance: tuple[str, ...] = ()

    @classmethod
    def from_finding(cls, finding: ResearchFinding) -> "ResearchEvidencePoint":
        provenance_map = _parse_provenance(finding.provenance)
        query = provenance_map.get("query", "")
        published_at = _parse_datetime_utc(provenance_map.get("published_at", ""))
        relevance_value = provenance_map.get("relevance_score", "")
        relevance_hint: float | None = None
        if relevance_value:
            try:
                relevance_hint = _clamp(float(relevance_value), lower=0.0, upper=1.0)
            except ValueError:
                relevance_hint = None
        return cls(
            summary=finding.summary,
            sentiment=float(_clamp(finding.sentiment, lower=-1.0, upper=1.0)),
            credibility=float(_clamp(finding.credibility, lower=0.01, upper=1.0)),
            source_name=finding.source_name,
            source_type=finding.source_type,
            published_at_utc=published_at,
            query=query,
            relevance_hint=relevance_hint,
            provenance=finding.provenance,
        )


@dataclass(slots=True, frozen=True)
class ResearchFeatureBundle:
    findings_count: int
    weighted_sentiment: float
    evidence_strength: float
    disagreement_score: float
    avg_credibility: float
    market_relevance_score: float
    timeliness_decay: float
    source_credibility_prior: float
    contradiction_score: float
    source_diversity: float
    entity_event_alignment_score: float
    evidence_novelty_score: float
    freshness_hours: float
    effective_credibility: float

    @classmethod
    def empty(cls) -> "ResearchFeatureBundle":
        return cls(
            findings_count=0,
            weighted_sentiment=0.0,
            evidence_strength=0.0,
            disagreement_score=0.0,
            avg_credibility=0.0,
            market_relevance_score=0.0,
            timeliness_decay=0.0,
            source_credibility_prior=0.0,
            contradiction_score=0.0,
            source_diversity=0.0,
            entity_event_alignment_score=0.0,
            evidence_novelty_score=0.0,
            freshness_hours=0.0,
            effective_credibility=0.0,
        )

    def to_runtime_dict(self) -> dict[str, float]:
        return {
            "research_findings_count": float(self.findings_count),
            "weighted_sentiment": self.weighted_sentiment,
            "evidence_strength": self.evidence_strength,
            "disagreement_score": self.disagreement_score,
            "avg_credibility": self.avg_credibility,
            "market_relevance_score": self.market_relevance_score,
            "timeliness_decay": self.timeliness_decay,
            "source_credibility_prior": self.source_credibility_prior,
            "contradiction_score": self.contradiction_score,
            "source_diversity": self.source_diversity,
            "entity_event_alignment_score": self.entity_event_alignment_score,
            "evidence_novelty_score": self.evidence_novelty_score,
            "freshness_hours": self.freshness_hours,
            "effective_credibility": self.effective_credibility,
        }

    def to_feature_row_dict(self) -> dict[str, float | int]:
        return {
            "f_research_findings_count": int(self.findings_count),
            "f_research_weighted_sentiment": round(self.weighted_sentiment, 8),
            "f_research_evidence_strength": round(self.evidence_strength, 8),
            "f_research_avg_credibility": round(self.avg_credibility, 8),
            "f_research_disagreement": round(self.disagreement_score, 8),
            "f_research_source_diversity": round(self.source_diversity, 8),
            "f_research_contradiction_rate": round(self.contradiction_score, 8),
            "f_research_conflict_score": round(self.contradiction_score, 8),
            "f_research_freshness_hours": round(self.freshness_hours, 8),
            "f_research_market_relevance": round(self.market_relevance_score, 8),
            "f_research_timeliness_decay": round(self.timeliness_decay, 8),
            "f_research_source_credibility_prior": round(self.source_credibility_prior, 8),
            "f_research_entity_event_alignment": round(self.entity_event_alignment_score, 8),
            "f_research_evidence_novelty": round(self.evidence_novelty_score, 8),
            "f_research_effective_credibility": round(self.effective_credibility, 8),
        }


@dataclass(slots=True, frozen=True)
class _NormalizedEvidencePoint:
    summary: str
    sentiment: float
    credibility: float
    prior: float
    effective_cred: float
    source_identity: str
    summary_tokens: set[str]
    relevance: float
    alignment: float
    age_hours: float
    decay: float
    source_type: SourceType
    published_at_utc: datetime | None


def build_research_feature_bundle(
    *,
    market_title: str,
    market_category: str,
    event_context: str,
    decision_timestamp_utc: datetime | None,
    evidence_points: Sequence[ResearchEvidencePoint],
) -> ResearchFeatureBundle:
    if not evidence_points:
        return ResearchFeatureBundle.empty()

    as_of = decision_timestamp_utc or datetime.now(UTC)
    market_tokens = _tokenize(f"{market_title} {market_category}")
    event_tokens = _tokenize(event_context) if event_context.strip() else set(market_tokens)

    normalized: list[_NormalizedEvidencePoint] = []
    for point in evidence_points:
        summary = point.summary.strip()
        if not summary:
            summary = point.query.strip()
        if not summary:
            summary = point.source_name.strip()
        if not summary:
            summary = point.source_type.value.lower()
        sentiment = float(_clamp(point.sentiment, lower=-1.0, upper=1.0))
        credibility = float(_clamp(point.credibility, lower=0.01, upper=1.0))
        prior = float(_source_prior(point.source_type, point.source_name))
        effective_cred = float(_clamp((credibility * 0.70) + (prior * 0.30), lower=0.01, upper=1.0))
        summary_tokens = _tokenize(summary)
        query_tokens = _tokenize(point.query)
        if point.relevance_hint is not None:
            relevance = float(_clamp(point.relevance_hint, lower=0.0, upper=1.0))
        else:
            relevance = max(
                _overlap_ratio(market_tokens, summary_tokens),
                _overlap_ratio(query_tokens, summary_tokens),
            )
        alignment = max(
            _overlap_ratio(event_tokens, summary_tokens),
            _jaccard(market_tokens, summary_tokens),
        )
        if point.published_at_utc is None:
            age_hours = 168.0
        else:
            age_hours = max((as_of - point.published_at_utc).total_seconds() / 3600.0, 0.0)
        decay = float(_clamp(math.exp(-math.log(2.0) * (age_hours / 72.0)), lower=0.0, upper=1.0))
        normalized.append(
            _NormalizedEvidencePoint(
                summary=summary,
                sentiment=sentiment,
                credibility=credibility,
                prior=prior,
                effective_cred=effective_cred,
                source_identity=(point.source_name.strip().lower() or point.source_type.value.lower()),
                summary_tokens=summary_tokens,
                relevance=relevance,
                alignment=alignment,
                age_hours=age_hours,
                decay=decay,
                source_type=point.source_type,
                published_at_utc=point.published_at_utc,
            )
        )

    if not normalized:
        return ResearchFeatureBundle.empty()

    normalized.sort(
        key=lambda item: (
            -item.effective_cred,
            str(item.published_at_utc or ""),
            item.source_identity,
            item.summary,
        )
    )

    raw_weights = [item.credibility for item in normalized]
    effective_weights = [item.effective_cred for item in normalized]
    raw_weight_total = max(sum(raw_weights), 1e-9)
    effective_weight_total = max(sum(effective_weights), 1e-9)

    weighted_sentiment = sum(
        item.sentiment * item.credibility
        for item in normalized
    ) / raw_weight_total
    avg_credibility = sum(item.credibility for item in normalized) / len(normalized)
    avg_prior = sum(item.prior for item in normalized) / len(normalized)
    effective_credibility = sum(item.effective_cred for item in normalized) / len(normalized)

    weighted_var = sum(
        item.credibility * ((item.sentiment - weighted_sentiment) ** 2)
        for item in normalized
    ) / raw_weight_total
    disagreement_score = _clamp(math.sqrt(max(weighted_var, 0.0)), lower=0.0, upper=1.0)

    market_relevance = sum(
        item.relevance * item.effective_cred
        for item in normalized
    ) / effective_weight_total
    entity_alignment = sum(
        item.alignment * item.effective_cred
        for item in normalized
    ) / effective_weight_total
    timeliness_decay = sum(
        item.decay * item.effective_cred
        for item in normalized
    ) / effective_weight_total
    freshness_hours = sum(
        item.age_hours * item.effective_cred
        for item in normalized
    ) / effective_weight_total

    source_counts: dict[str, int] = {}
    source_types = {item.source_type for item in normalized}
    for item in normalized:
        identity = item.source_identity
        source_counts[identity] = source_counts.get(identity, 0) + 1
    unique_sources = len(source_counts)
    normalized_source_count = _clamp(unique_sources / 5.0, lower=0.0, upper=1.0)
    total = len(normalized)
    simpson = 0.0
    if total > 0:
        simpson = 1.0 - sum((count / total) ** 2 for count in source_counts.values())
    max_simpson = 1.0 - (1.0 / unique_sources) if unique_sources > 1 else 1.0
    simpson_norm = _clamp(simpson / max(max_simpson, 1e-9), lower=0.0, upper=1.0) if unique_sources > 1 else 0.0
    source_diversity = _clamp((normalized_source_count * 0.50) + (simpson_norm * 0.50), lower=0.0, upper=1.0)

    pair_weight_total = 0.0
    conflict_weight = 0.0
    for idx in range(len(normalized)):
        left = normalized[idx]
        left_w = left.effective_cred
        left_s = left.sentiment
        for jdx in range(idx + 1, len(normalized)):
            right = normalized[jdx]
            right_w = right.effective_cred
            right_s = right.sentiment
            pair_weight = left_w * right_w
            pair_weight_total += pair_weight
            if (left_s > 0.05 and right_s < -0.05) or (left_s < -0.05 and right_s > 0.05):
                conflict_weight += pair_weight * min(abs(left_s), abs(right_s))
    contradiction_score = conflict_weight / pair_weight_total if pair_weight_total > 0 else 0.0
    contradiction_score = _clamp(contradiction_score, lower=0.0, upper=1.0)

    seen_tokens: set[str] = set()
    novelty_weighted = 0.0
    for item in normalized:
        tokens = item.summary_tokens
        if not tokens:
            novelty = 0.0
        elif not seen_tokens:
            novelty = 1.0
        else:
            novelty = len(tokens - seen_tokens) / len(tokens)
        novelty_weighted += novelty * item.effective_cred
        seen_tokens |= tokens
    evidence_novelty = novelty_weighted / effective_weight_total if effective_weight_total > 0 else 0.0
    evidence_novelty = _clamp(evidence_novelty, lower=0.0, upper=1.0)

    count_factor = _clamp(len(normalized) / 6.0, lower=0.0, upper=1.0)
    type_diversity = _clamp(len(source_types) / 4.0, lower=0.0, upper=1.0)
    evidence_strength = _clamp(
        (avg_credibility * 0.25)
        + (count_factor * 0.15)
        + (type_diversity * 0.10)
        + (source_diversity * 0.10)
        + (market_relevance * 0.18)
        + (timeliness_decay * 0.12)
        + (avg_prior * 0.10),
        lower=0.0,
        upper=1.0,
    )

    return ResearchFeatureBundle(
        findings_count=len(normalized),
        weighted_sentiment=float(_clamp(weighted_sentiment, lower=-1.0, upper=1.0)),
        evidence_strength=float(evidence_strength),
        disagreement_score=float(disagreement_score),
        avg_credibility=float(_clamp(avg_credibility, lower=0.0, upper=1.0)),
        market_relevance_score=float(_clamp(market_relevance, lower=0.0, upper=1.0)),
        timeliness_decay=float(_clamp(timeliness_decay, lower=0.0, upper=1.0)),
        source_credibility_prior=float(_clamp(avg_prior, lower=0.0, upper=1.0)),
        contradiction_score=float(contradiction_score),
        source_diversity=float(source_diversity),
        entity_event_alignment_score=float(_clamp(entity_alignment, lower=0.0, upper=1.0)),
        evidence_novelty_score=float(evidence_novelty),
        freshness_hours=float(max(freshness_hours, 0.0)),
        effective_credibility=float(_clamp(effective_credibility, lower=0.0, upper=1.0)),
    )


def build_bundle_from_findings(
    *,
    market_title: str,
    market_category: str,
    event_context: str,
    decision_timestamp_utc: datetime | None,
    findings: Sequence[ResearchFinding],
) -> ResearchFeatureBundle:
    evidence = tuple(ResearchEvidencePoint.from_finding(finding) for finding in findings)
    return build_research_feature_bundle(
        market_title=market_title,
        market_category=market_category,
        event_context=event_context,
        decision_timestamp_utc=decision_timestamp_utc,
        evidence_points=evidence,
    )
