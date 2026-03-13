from __future__ import annotations

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket


class PredictionAgent:
    name = "prediction-agent"

    def __init__(self, settings: PredictionSettings) -> None:
        self.settings = settings

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        market_yes_prob = candidate.market.yes_price
        research_yes_prob, research_reliability = self._research_yes_probability(research)
        structure_yes_prob = self._structure_yes_probability(candidate)

        market_weight, research_weight, structure_weight = self._normalized_weights()
        fair_yes_prob = self._clamp_probability(
            (market_yes_prob * market_weight)
            + (research_yes_prob * research_weight)
            + (structure_yes_prob * structure_weight)
        )

        if fair_yes_prob >= 0.5:
            selected_side = OutcomeSide.YES
            selected_market_price = candidate.market.yes_price
            selected_fair_price = fair_yes_prob
        else:
            selected_side = OutcomeSide.NO
            selected_market_price = candidate.market.no_price
            selected_fair_price = 1.0 - fair_yes_prob

        edge = selected_fair_price - selected_market_price
        confidence = self._confidence(
            edge=edge,
            research_reliability=research_reliability,
            scan_score=candidate.scan_score,
        )

        rationale = (
            f"market_yes_prob={market_yes_prob:.4f}",
            f"research_yes_prob={research_yes_prob:.4f}",
            f"structure_yes_prob={structure_yes_prob:.4f}",
            f"weights=({market_weight:.3f},{research_weight:.3f},{structure_weight:.3f})",
            f"research_reliability={research_reliability:.4f}",
            f"selected_side={selected_side.value}",
            f"selected_market_price={selected_market_price:.4f}",
            f"selected_fair_price={selected_fair_price:.4f}",
            f"edge={edge:.4f}",
            f"confidence={confidence:.4f}",
        )

        return PredictionResult(
            market_id=candidate.market.market_id,
            selected_side=selected_side,
            market_yes_prob=round(market_yes_prob, 4),
            fair_yes_prob=round(fair_yes_prob, 4),
            selected_market_price=round(selected_market_price, 4),
            selected_fair_price=round(selected_fair_price, 4),
            edge=round(edge, 4),
            confidence=round(confidence, 4),
            rationale=rationale,
        )

    def _normalized_weights(self) -> tuple[float, float, float]:
        raw = (
            max(self.settings.market_weight, 0.0),
            max(self.settings.narrative_weight, 0.0),
            max(self.settings.structure_weight, 0.0),
        )
        total = raw[0] + raw[1] + raw[2]
        if total <= 0.0:
            return (1.0, 0.0, 0.0)
        return (raw[0] / total, raw[1] / total, raw[2] / total)

    def _research_yes_probability(self, research: ResearchPacket) -> tuple[float, float]:
        research_reliability = self._clamp_01(research.evidence_strength * (1.0 - research.disagreement_score))
        # Sentiment contribution is dampened by reliability to keep the dry-run stable.
        shift = research.weighted_sentiment * (0.45 * research_reliability)
        research_yes_prob = self._clamp_probability(0.5 + shift)
        return research_yes_prob, research_reliability

    @staticmethod
    def _structure_yes_probability(candidate: MarketCandidate) -> float:
        return PredictionAgent._clamp_probability(0.5 + ((candidate.scan_score - 0.5) * 0.20))

    @staticmethod
    def _confidence(*, edge: float, research_reliability: float, scan_score: float) -> float:
        edge_strength = PredictionAgent._clamp_01(abs(edge) / 0.25)
        value = (research_reliability * 0.45) + (edge_strength * 0.35) + (scan_score * 0.20)
        return PredictionAgent._clamp_01(value)

    @staticmethod
    def _clamp_probability(value: float) -> float:
        return min(max(value, 0.01), 0.99)

    @staticmethod
    def _clamp_01(value: float) -> float:
        return min(max(value, 0.0), 1.0)
