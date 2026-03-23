from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .baselines import BaselinePrediction
from .models import LabelRecord, clamp

ABLATION_VARIANT_NAMES: tuple[str, ...] = (
    "market_only_baseline",
    "market_plus_research_baseline",
    "market_plus_news",
    "market_plus_reddit",
    "market_plus_x",
    "market_plus_alt_data_without_llm",
    "market_plus_alt_data_with_llm_enrichment",
)

_ALT_ONLY_VARIANTS: frozenset[str] = frozenset(
    {
        "market_plus_news",
        "market_plus_reddit",
        "market_plus_x",
        "market_plus_alt_data_without_llm",
        "market_plus_alt_data_with_llm_enrichment",
    }
)


@dataclass(slots=True, frozen=True)
class AltFeatureRow:
    row_id: str
    news_volume_24h: float
    news_burstiness_24h: float
    source_diversity: float
    freshness_decay: float
    source_credibility_prior: float
    contradiction_score: float
    novelty_score: float
    catalyst_strength_score: float
    reddit_mentions_24h: float
    reddit_attention: float
    x_mentions_24h: float
    x_attention: float
    market_linked_coverage: float
    enrichment_coverage: float

    @classmethod
    def zero(cls, row_id: str) -> "AltFeatureRow":
        return cls(
            row_id=row_id,
            news_volume_24h=0.0,
            news_burstiness_24h=0.0,
            source_diversity=0.0,
            freshness_decay=0.0,
            source_credibility_prior=0.0,
            contradiction_score=0.0,
            novelty_score=0.0,
            catalyst_strength_score=0.0,
            reddit_mentions_24h=0.0,
            reddit_attention=0.0,
            x_mentions_24h=0.0,
            x_attention=0.0,
            market_linked_coverage=0.0,
            enrichment_coverage=0.0,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AltFeatureRow | None":
        row_id = str(payload.get("row_id") or "").strip()
        if not row_id:
            return None
        return cls(
            row_id=row_id,
            news_volume_24h=_to_float(payload.get("f_alt_news_volume_24h")),
            news_burstiness_24h=_to_float(payload.get("f_alt_news_burstiness_24h")),
            source_diversity=_to_float(payload.get("f_alt_source_diversity")),
            freshness_decay=_to_float(payload.get("f_alt_freshness_decay")),
            source_credibility_prior=_to_float(payload.get("f_alt_source_credibility_prior")),
            contradiction_score=_to_float(payload.get("f_alt_contradiction_score")),
            novelty_score=_to_float(payload.get("f_alt_novelty_score")),
            catalyst_strength_score=_to_float(payload.get("f_alt_catalyst_strength_score")),
            reddit_mentions_24h=_to_float(payload.get("f_alt_reddit_mentions_24h")),
            reddit_attention=_to_float(payload.get("f_alt_reddit_attention")),
            x_mentions_24h=_to_float(payload.get("f_alt_x_mentions_24h")),
            x_attention=_to_float(payload.get("f_alt_x_attention")),
            market_linked_coverage=_to_float(payload.get("f_alt_market_linked_coverage")),
            enrichment_coverage=_to_float(payload.get("f_alt_enrichment_coverage")),
        )


@dataclass(slots=True, frozen=True)
class LinearLogitModel:
    means: tuple[float, ...]
    stds: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float


def uses_alt_features(variant: str) -> bool:
    return variant in _ALT_ONLY_VARIANTS


def load_alt_feature_rows(path: Path) -> dict[str, AltFeatureRow]:
    if not path.exists():
        return {}
    rows: dict[str, AltFeatureRow] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, Mapping):
                continue
            row = AltFeatureRow.from_dict(payload)
            if row is not None:
                rows[row.row_id] = row
    return rows


class AblationPredictorSuite:
    def train_model(
        self,
        *,
        variant: str,
        train_rows: Sequence[LabelRecord],
        alt_rows_by_id: Mapping[str, AltFeatureRow],
        iterations: int = 350,
        learning_rate: float = 0.08,
        l2_penalty: float = 1e-3,
    ) -> LinearLogitModel:
        vectors: list[tuple[float, ...]] = []
        labels: list[int] = []
        for row in train_rows:
            alt_row = alt_rows_by_id.get(row.row_id, AltFeatureRow.zero(row.row_id))
            vectors.append(self._feature_vector(variant=variant, row=row, alt=alt_row))
            labels.append(row.label_yes)
        return _fit_logistic_regression(
            vectors=vectors,
            labels=labels,
            iterations=iterations,
            learning_rate=learning_rate,
            l2_penalty=l2_penalty,
        )

    def predict(
        self,
        *,
        variant: str,
        row: LabelRecord,
        alt_row: AltFeatureRow,
        model: LinearLogitModel,
    ) -> BaselinePrediction:
        vector = self._feature_vector(variant=variant, row=row, alt=alt_row)
        yes_prob = _predict_probability(model=model, vector=vector)
        confidence = clamp(abs(yes_prob - 0.5) * 2.0, lower=0.0, upper=1.0)
        return _make_prediction(
            variant_name=variant,
            row=row,
            predicted_yes_prob=yes_prob,
            confidence=confidence,
        )

    @staticmethod
    def _feature_vector(*, variant: str, row: LabelRecord, alt: AltFeatureRow) -> tuple[float, ...]:
        market_logit = _safe_logit(row.market_yes_prob_at_decision)
        research_vector = (
            clamp(row.research_weighted_sentiment, lower=-1.0, upper=1.0),
            clamp(row.research_evidence_strength, lower=0.0, upper=1.0),
            clamp(row.research_disagreement_score, lower=0.0, upper=1.0),
            clamp(math.log1p(max(row.research_findings_count, 0)) / math.log1p(50.0), lower=0.0, upper=1.0),
        )
        news_vector = (
            clamp(math.log1p(max(alt.news_volume_24h, 0.0)) / math.log1p(80.0), lower=0.0, upper=1.0),
            clamp(alt.news_burstiness_24h / 10.0, lower=-1.0, upper=1.0),
            clamp(alt.freshness_decay, lower=0.0, upper=1.0),
            clamp(alt.source_credibility_prior, lower=0.0, upper=1.0),
            clamp(alt.market_linked_coverage, lower=0.0, upper=1.0),
        )
        reddit_vector = (
            clamp(math.log1p(max(alt.reddit_mentions_24h, 0.0)) / math.log1p(80.0), lower=0.0, upper=1.0),
            clamp(alt.reddit_attention / 6.0, lower=0.0, upper=1.0),
            clamp(alt.source_diversity, lower=0.0, upper=1.0),
            clamp(alt.market_linked_coverage, lower=0.0, upper=1.0),
        )
        x_vector = (
            clamp(math.log1p(max(alt.x_mentions_24h, 0.0)) / math.log1p(80.0), lower=0.0, upper=1.0),
            clamp(alt.x_attention / 6.0, lower=0.0, upper=1.0),
            clamp(alt.source_diversity, lower=0.0, upper=1.0),
            clamp(alt.market_linked_coverage, lower=0.0, upper=1.0),
        )
        llm_vector = (
            clamp(alt.contradiction_score, lower=0.0, upper=1.0),
            clamp(alt.novelty_score, lower=0.0, upper=1.0),
            clamp(alt.catalyst_strength_score, lower=0.0, upper=1.0),
            clamp(alt.enrichment_coverage, lower=0.0, upper=1.0),
        )

        if variant == "market_only_baseline":
            return (market_logit,)
        if variant == "market_plus_research_baseline":
            return (market_logit, *research_vector)
        if variant == "market_plus_news":
            return (market_logit, *news_vector)
        if variant == "market_plus_reddit":
            return (market_logit, *reddit_vector)
        if variant == "market_plus_x":
            return (market_logit, *x_vector)
        if variant == "market_plus_alt_data_without_llm":
            return (
                market_logit,
                *news_vector,
                *reddit_vector,
                *x_vector,
                clamp(alt.source_diversity, lower=0.0, upper=1.0),
                clamp(alt.source_credibility_prior, lower=0.0, upper=1.0),
            )
        if variant == "market_plus_alt_data_with_llm_enrichment":
            return (
                market_logit,
                *news_vector,
                *reddit_vector,
                *x_vector,
                clamp(alt.source_diversity, lower=0.0, upper=1.0),
                clamp(alt.source_credibility_prior, lower=0.0, upper=1.0),
                *llm_vector,
            )
        raise ValueError(f"Unsupported ablation variant: {variant}")


def _fit_logistic_regression(
    *,
    vectors: Sequence[tuple[float, ...]],
    labels: Sequence[int],
    iterations: int,
    learning_rate: float,
    l2_penalty: float,
) -> LinearLogitModel:
    if not vectors:
        return LinearLogitModel(means=(), stds=(), weights=(), bias=0.0)
    feature_count = len(vectors[0])
    if feature_count == 0:
        return LinearLogitModel(means=(), stds=(), weights=(), bias=0.0)

    means: list[float] = []
    stds: list[float] = []
    for idx in range(feature_count):
        values = [row[idx] for row in vectors]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = math.sqrt(max(variance, 1e-12))
        means.append(mean)
        stds.append(std)

    normalized = [
        tuple((row[idx] - means[idx]) / stds[idx] for idx in range(feature_count))
        for row in vectors
    ]
    weights = [0.0 for _ in range(feature_count)]
    bias = 0.0
    sample_count = len(normalized)

    for _ in range(max(iterations, 1)):
        grad_w = [0.0 for _ in range(feature_count)]
        grad_b = 0.0
        for vector, label in zip(normalized, labels):
            score = bias + sum(weight * value for weight, value in zip(weights, vector))
            prediction = _sigmoid(score)
            error = prediction - float(label)
            grad_b += error
            for idx in range(feature_count):
                grad_w[idx] += error * vector[idx]
        grad_b /= sample_count
        for idx in range(feature_count):
            grad_w[idx] = (grad_w[idx] / sample_count) + (l2_penalty * weights[idx])
            weights[idx] -= learning_rate * grad_w[idx]
        bias -= learning_rate * grad_b

    return LinearLogitModel(
        means=tuple(means),
        stds=tuple(stds),
        weights=tuple(weights),
        bias=bias,
    )


def _predict_probability(*, model: LinearLogitModel, vector: Sequence[float]) -> float:
    if not model.weights:
        return 0.5
    normalized = [
        (value - model.means[idx]) / model.stds[idx]
        for idx, value in enumerate(vector)
    ]
    score = model.bias + sum(weight * value for weight, value in zip(model.weights, normalized))
    return clamp(_sigmoid(score), lower=0.01, upper=0.99)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _safe_logit(probability: float) -> float:
    prob = clamp(probability, lower=0.01, upper=0.99)
    return math.log(prob / (1.0 - prob))


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0
    return 0.0


def _make_prediction(
    *,
    variant_name: str,
    row: LabelRecord,
    predicted_yes_prob: float,
    confidence: float,
) -> BaselinePrediction:
    yes_prob = clamp(predicted_yes_prob, lower=0.01, upper=0.99)
    if yes_prob >= 0.5:
        selected_side = "YES"
        selected_market_price = row.market_yes_prob_at_decision
        selected_fair_price = yes_prob
    else:
        selected_side = "NO"
        selected_market_price = 1.0 - row.market_yes_prob_at_decision
        selected_fair_price = 1.0 - yes_prob
    edge = selected_fair_price - selected_market_price
    return BaselinePrediction(
        baseline_name=variant_name,
        row_id=row.row_id,
        predicted_yes_prob=round(yes_prob, 8),
        selected_side=selected_side,
        selected_market_price=round(selected_market_price, 8),
        selected_fair_price=round(selected_fair_price, 8),
        edge=round(edge, 8),
        confidence=round(clamp(confidence, lower=0.0, upper=1.0), 8),
        market_yes_prob_at_decision=round(row.market_yes_prob_at_decision, 8),
        label_yes=row.label_yes,
    )
