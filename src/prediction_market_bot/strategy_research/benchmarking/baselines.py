from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.domain.enums import MarketStatus, OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, ResearchPacket

from .models import LabelRecord, clamp

BASELINE_NAMES: tuple[str, ...] = (
    "market_implied",
    "fifty_fifty",
    "category_prior",
    "heuristic_prediction_agent",
    "research_only",
    "momentum_structure",
)


@dataclass(slots=True, frozen=True)
class BaselineTrainingStats:
    global_prior_yes: float
    category_prior_yes: Mapping[str, float]


@dataclass(slots=True, frozen=True)
class BaselinePrediction:
    baseline_name: str
    row_id: str
    predicted_yes_prob: float
    selected_side: str
    selected_market_price: float
    selected_fair_price: float
    edge: float
    confidence: float
    market_yes_prob_at_decision: float
    label_yes: int


def fit_training_stats(train_rows: list[LabelRecord]) -> BaselineTrainingStats:
    if not train_rows:
        return BaselineTrainingStats(global_prior_yes=0.5, category_prior_yes={})
    global_prior = sum(row.label_yes for row in train_rows) / len(train_rows)
    by_category_sum: dict[str, float] = {}
    by_category_count: dict[str, int] = {}
    for row in train_rows:
        category = row.category.strip() or "unknown"
        by_category_sum[category] = by_category_sum.get(category, 0.0) + float(row.label_yes)
        by_category_count[category] = by_category_count.get(category, 0) + 1
    category_priors = {
        category: by_category_sum[category] / by_category_count[category]
        for category in by_category_sum
    }
    return BaselineTrainingStats(
        global_prior_yes=clamp(global_prior, lower=0.0, upper=1.0),
        category_prior_yes=category_priors,
    )


class BaselinePredictorSuite:
    def __init__(self, prediction_settings: PredictionSettings) -> None:
        self._prediction_agent = PredictionAgent(prediction_settings)

    def predict_all(self, row: LabelRecord, *, training_stats: BaselineTrainingStats) -> dict[str, BaselinePrediction]:
        predictions: dict[str, BaselinePrediction] = {}
        predictions["market_implied"] = self._make_prediction("market_implied", row, row.market_yes_prob_at_decision)
        predictions["fifty_fifty"] = self._make_prediction("fifty_fifty", row, 0.5, confidence=0.0)
        category_prior = training_stats.category_prior_yes.get(row.category, training_stats.global_prior_yes)
        predictions["category_prior"] = self._make_prediction("category_prior", row, category_prior)
        predictions["heuristic_prediction_agent"] = self._heuristic_prediction_agent(row)
        predictions["research_only"] = self._research_only(row)
        predictions["momentum_structure"] = self._momentum_structure(row)
        return predictions

    def _heuristic_prediction_agent(self, row: LabelRecord) -> BaselinePrediction:
        snapshot = MarketSnapshot.from_yes_price(
            market_id=row.market_id,
            venue="offline_benchmark",
            title=row.market_title or row.market_id,
            yes_price=row.market_yes_prob_at_decision,
            liquidity_usd=max(row.liquidity_usd, 0.0),
            volume_24h_usd=max(row.volume_24h_usd, 0.0),
            spread_bps=0,
            hours_to_resolution=0.0,
            last_price_move_bps=int(round(row.structure_momentum * 10_000)),
            category=row.category or "unknown",
            status=MarketStatus.RESOLVED,
            updated_at=row.decision_timestamp_utc,
        )
        candidate = MarketCandidate(
            market=snapshot,
            scan_score=clamp(row.scan_score, lower=0.0, upper=1.0),
            reasons=("offline_label_reconstruction",),
        )
        packet = ResearchPacket(
            market_id=row.market_id,
            findings=(),
            weighted_sentiment=clamp(row.research_weighted_sentiment, lower=-1.0, upper=1.0),
            evidence_strength=clamp(row.research_evidence_strength, lower=0.0, upper=1.0),
            disagreement_score=clamp(row.research_disagreement_score, lower=0.0, upper=1.0),
            narrative_summary="offline benchmark reconstructed research packet",
        )
        result = self._prediction_agent.run(candidate, packet)
        return self._make_prediction(
            "heuristic_prediction_agent",
            row,
            result.fair_yes_prob,
            confidence=result.confidence,
        )

    def _research_only(self, row: LabelRecord) -> BaselinePrediction:
        reliability = clamp(
            row.research_evidence_strength * (1.0 - row.research_disagreement_score),
            lower=0.0,
            upper=1.0,
        )
        yes_prob = clamp(
            0.5 + (row.research_weighted_sentiment * (0.45 * reliability)),
            lower=0.01,
            upper=0.99,
        )
        confidence = clamp((reliability * 0.65) + (abs(yes_prob - 0.5) * 0.70), lower=0.0, upper=1.0)
        return self._make_prediction("research_only", row, yes_prob, confidence=confidence)

    def _momentum_structure(self, row: LabelRecord) -> BaselinePrediction:
        yes_prob = clamp(
            0.5 + (row.structure_momentum * 0.65) + ((row.structure_score - 0.5) * 0.30),
            lower=0.01,
            upper=0.99,
        )
        confidence = clamp((abs(row.structure_momentum) * 1.2) + (row.structure_score * 0.35), lower=0.0, upper=1.0)
        return self._make_prediction("momentum_structure", row, yes_prob, confidence=confidence)

    @staticmethod
    def _make_prediction(
        baseline_name: str,
        row: LabelRecord,
        predicted_yes_prob: float,
        *,
        confidence: float | None = None,
    ) -> BaselinePrediction:
        yes_prob = clamp(predicted_yes_prob, lower=0.01, upper=0.99)
        if yes_prob >= 0.5:
            side = OutcomeSide.YES
            selected_market_price = row.market_yes_prob_at_decision
            selected_fair_price = yes_prob
        else:
            side = OutcomeSide.NO
            selected_market_price = 1.0 - row.market_yes_prob_at_decision
            selected_fair_price = 1.0 - yes_prob
        edge = selected_fair_price - selected_market_price
        effective_confidence = confidence
        if effective_confidence is None:
            effective_confidence = clamp(abs(yes_prob - 0.5) * 2.0, lower=0.0, upper=1.0)
        return BaselinePrediction(
            baseline_name=baseline_name,
            row_id=row.row_id,
            predicted_yes_prob=round(yes_prob, 6),
            selected_side=side.value,
            selected_market_price=round(selected_market_price, 6),
            selected_fair_price=round(selected_fair_price, 6),
            edge=round(edge, 6),
            confidence=round(clamp(effective_confidence, lower=0.0, upper=1.0), 6),
            market_yes_prob_at_decision=round(row.market_yes_prob_at_decision, 6),
            label_yes=row.label_yes,
        )
