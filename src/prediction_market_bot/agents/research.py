from __future__ import annotations

import logging
from math import sqrt
from typing import Sequence

from prediction_market_bot.domain.models import MarketCandidate, ResearchFinding, ResearchPacket
from prediction_market_bot.interfaces import ResearchSource
from prediction_market_bot.services.research_features import build_bundle_from_findings

logger = logging.getLogger(__name__)


class ResearchAgent:
    name = "research-agent"

    def __init__(self, sources: Sequence[ResearchSource]) -> None:
        self.sources = tuple(sources)
        self.last_source_failures: int = 0

    def run(self, candidate: MarketCandidate) -> ResearchPacket:
        self.last_source_failures = 0
        findings = self._collect_findings(candidate)
        if not findings:
            return ResearchPacket(
                market_id=candidate.market.market_id,
                findings=(),
                weighted_sentiment=0.0,
                evidence_strength=0.0,
                disagreement_score=0.0,
                narrative_summary="No evidence found.",
                feature_bundle={},
            )

        weighted_sentiment = self._weighted_sentiment(findings)
        evidence_strength = self._evidence_strength(findings)
        disagreement_score = self._disagreement_score(findings, weighted_sentiment)
        narrative_summary = self._build_summary(findings)
        bundle = build_bundle_from_findings(
            market_title=candidate.market.title,
            market_category=candidate.market.category,
            event_context=candidate.market.category,
            decision_timestamp_utc=candidate.market.updated_at,
            findings=findings,
        )

        return ResearchPacket(
            market_id=candidate.market.market_id,
            findings=findings,
            weighted_sentiment=round(weighted_sentiment, 4),
            evidence_strength=round(evidence_strength, 4),
            disagreement_score=round(disagreement_score, 4),
            narrative_summary=narrative_summary,
            feature_bundle=bundle.to_runtime_dict(),
        )

    def _collect_findings(self, candidate: MarketCandidate) -> tuple[ResearchFinding, ...]:
        buffer: list[ResearchFinding] = []
        for source in self.sources:
            try:
                fetched = source.fetch(candidate.market)
            except Exception as exc:
                self.last_source_failures += 1
                logger.warning(
                    "research_source_failed",
                    extra={
                        "event": "research_source_failed",
                        "market_id": candidate.market.market_id,
                        "source_type": type(source).__name__,
                        "error": str(exc),
                    },
                )
                continue
            buffer.extend(fetched)
        return tuple(
            sorted(
                buffer,
                key=lambda finding: (
                    finding.source_type.value,
                    finding.source_name,
                    finding.summary,
                    finding.sentiment,
                    finding.credibility,
                ),
            )
        )

    @staticmethod
    def _weighted_sentiment(findings: Sequence[ResearchFinding]) -> float:
        weighted_sum = 0.0
        weight_total = 0.0
        for item in findings:
            weight = max(item.credibility, 0.01)
            weighted_sum += item.sentiment * weight
            weight_total += weight
        return weighted_sum / weight_total if weight_total else 0.0

    @staticmethod
    def _evidence_strength(findings: Sequence[ResearchFinding]) -> float:
        credibility_avg = sum(item.credibility for item in findings) / len(findings)
        count_factor = min(len(findings) / 6.0, 1.0)
        diversity_factor = min(len({item.source_type for item in findings}) / 4.0, 1.0)
        value = (credibility_avg * 0.40) + (count_factor * 0.40) + (diversity_factor * 0.20)
        return min(max(value, 0.0), 1.0)

    @staticmethod
    def _disagreement_score(findings: Sequence[ResearchFinding], weighted_sentiment: float) -> float:
        if len(findings) == 1:
            return 0.0
        weight_total = 0.0
        weighted_squared_error = 0.0
        for item in findings:
            weight = max(item.credibility, 0.01)
            weight_total += weight
            weighted_squared_error += weight * ((item.sentiment - weighted_sentiment) ** 2)
        variance = weighted_squared_error / weight_total if weight_total else 0.0
        return min(max(sqrt(variance), 0.0), 1.0)

    @staticmethod
    def _build_summary(findings: Sequence[ResearchFinding]) -> str:
        top = sorted(findings, key=lambda item: (-item.credibility, item.source_name, item.summary))[:3]
        return "; ".join(item.summary for item in top) or "No summary available."
