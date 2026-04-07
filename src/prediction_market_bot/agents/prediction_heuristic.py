"""Heuristic prediction path -- weighted blend of market/research/structure signals."""
from __future__ import annotations

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, PredictionResult, ResearchPacket


def run_heuristic(
    settings: PredictionSettings,
    candidate: MarketCandidate,
    research: ResearchPacket,
) -> PredictionResult:
    market_yes_prob = candidate.market.yes_price
    research_yes_prob, research_reliability = _research_yes_probability(research)
    structure_yes_prob = _structure_yes_probability(candidate)

    market_weight, research_weight, structure_weight = _normalized_weights(settings)
    fair_yes_prob = clamp_probability(
        (market_yes_prob * market_weight)
        + (research_yes_prob * research_weight)
        + (structure_yes_prob * structure_weight)
    )

    if fair_yes_prob >= candidate.market.yes_price:
        selected_side = OutcomeSide.YES
        selected_market_price = candidate.market.yes_price
        selected_fair_price = fair_yes_prob
    else:
        selected_side = OutcomeSide.NO
        selected_market_price = candidate.market.no_price
        selected_fair_price = 1.0 - fair_yes_prob

    edge = selected_fair_price - selected_market_price
    confidence = _confidence(
        edge=edge,
        research_reliability=research_reliability,
        scan_score=candidate.scan_score,
    )

    rationale_parts = [
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
    ]
    if settings.forced_heuristic_reason.strip():
        rationale_parts.append(f"forced_heuristic_reason={settings.forced_heuristic_reason.strip()}")
    rationale = tuple(rationale_parts)

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


def _normalized_weights(settings: PredictionSettings) -> tuple[float, float, float]:
    raw = (
        max(settings.market_weight, 0.0),
        max(settings.narrative_weight, 0.0),
        max(settings.structure_weight, 0.0),
    )
    total = raw[0] + raw[1] + raw[2]
    if total <= 0.0:
        return (1.0, 0.0, 0.0)
    return (raw[0] / total, raw[1] / total, raw[2] / total)


def _research_yes_probability(research: ResearchPacket) -> tuple[float, float]:
    research_reliability = clamp_01(research.evidence_strength * (1.0 - research.disagreement_score))
    shift = research.weighted_sentiment * (0.45 * research_reliability)
    research_yes_prob = clamp_probability(0.5 + shift)
    return research_yes_prob, research_reliability


def _structure_yes_probability(candidate: MarketCandidate) -> float:
    return clamp_probability(0.5 + ((candidate.scan_score - 0.5) * 0.20))


def _confidence(*, edge: float, research_reliability: float, scan_score: float) -> float:
    edge_strength = clamp_01(abs(edge) / 0.25)
    value = (research_reliability * 0.45) + (edge_strength * 0.35) + (scan_score * 0.20)
    return clamp_01(value)


def clamp_probability(value: float) -> float:
    return min(max(value, 0.01), 0.99)


def clamp_01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def prediction_from_fair_probability(
    *,
    candidate: MarketCandidate,
    fair_yes_prob: float,
    confidence: float,
    rationale: tuple[str, ...],
) -> PredictionResult:
    if fair_yes_prob >= candidate.market.yes_price:
        selected_side = OutcomeSide.YES
        selected_market_price = candidate.market.yes_price
        selected_fair_price = fair_yes_prob
    else:
        selected_side = OutcomeSide.NO
        selected_market_price = candidate.market.no_price
        selected_fair_price = 1.0 - fair_yes_prob
    edge = selected_fair_price - selected_market_price
    return PredictionResult(
        market_id=candidate.market.market_id,
        selected_side=selected_side,
        market_yes_prob=round(candidate.market.yes_price, 4),
        fair_yes_prob=round(fair_yes_prob, 4),
        selected_market_price=round(selected_market_price, 4),
        selected_fair_price=round(selected_fair_price, 4),
        edge=round(edge, 4),
        confidence=round(confidence, 4),
        rationale=rationale + (
            f"selected_side={selected_side.value}",
            f"selected_market_price={selected_market_price:.4f}",
            f"selected_fair_price={selected_fair_price:.4f}",
            f"edge={edge:.4f}",
        ),
    )


def edge_for_side(*, candidate: MarketCandidate, fair_yes_prob: float) -> float:
    if fair_yes_prob >= candidate.market.yes_price:
        return fair_yes_prob - candidate.market.yes_price
    return (1.0 - fair_yes_prob) - candidate.market.no_price


def prediction_approved(
    prediction: PredictionResult | None,
    *,
    min_confidence: float,
    min_edge_bps: float,
) -> bool | None:
    if prediction is None:
        return None
    edge_bps = prediction.edge * 10_000.0
    return bool(
        prediction.confidence >= min_confidence
        and edge_bps >= min_edge_bps
    )


def disagreement_bucket(*, fair_yes_delta: float | None, selected_side_changed: bool) -> str:
    if fair_yes_delta is None:
        return "model_missing"
    abs_delta = abs(float(fair_yes_delta))
    if selected_side_changed:
        if abs_delta >= 0.20:
            return "flip_large"
        return "flip_small"
    if abs_delta < 0.02:
        return "aligned"
    if abs_delta < 0.05:
        return "minor_drift"
    if abs_delta < 0.10:
        return "moderate_drift"
    if abs_delta < 0.20:
        return "major_drift"
    return "extreme_drift"
