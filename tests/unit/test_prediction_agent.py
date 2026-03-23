from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, ResearchFinding, ResearchPacket


def _candidate(market_id: str, *, yes_price: float, scan_score: float) -> MarketCandidate:
    snapshot = MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue="polymarket",
        title=f"Prediction market {market_id}",
        yes_price=yes_price,
        liquidity_usd=40_000,
        volume_24h_usd=30_000,
        spread_bps=110,
        hours_to_resolution=16,
        last_price_move_bps=10,
        category="test",
    )
    return MarketCandidate(market=snapshot, scan_score=scan_score, reasons=("ranked",))


def _research_packet(
    market_id: str,
    *,
    weighted_sentiment: float,
    evidence_strength: float,
    disagreement_score: float,
) -> ResearchPacket:
    return ResearchPacket(
        market_id=market_id,
        findings=(
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="rss",
                summary="mock",
                sentiment=weighted_sentiment,
                credibility=max(min(evidence_strength, 1.0), 0.01),
            ),
        ),
        weighted_sentiment=weighted_sentiment,
        evidence_strength=evidence_strength,
        disagreement_score=disagreement_score,
        narrative_summary="mock",
    )


def test_prediction_agent_combines_market_and_research_deterministically_yes_side() -> None:
    agent = PredictionAgent(PredictionSettings())
    candidate = _candidate("m-yes", yes_price=0.40, scan_score=0.80)
    research = _research_packet(
        "m-yes",
        weighted_sentiment=0.50,
        evidence_strength=0.90,
        disagreement_score=0.10,
    )

    result = agent.run(candidate, research)
    assert result.selected_side.value == "YES"
    assert result.fair_yes_prob == 0.543
    assert result.edge == 0.143
    assert result.confidence == 0.7247
    assert any("market_yes_prob=0.4000" in item for item in result.rationale)
    assert any("research_yes_prob=0.6823" in item for item in result.rationale)
    assert any("selected_side=YES" in item for item in result.rationale)


def test_prediction_agent_combines_market_and_research_deterministically_no_side() -> None:
    agent = PredictionAgent(PredictionSettings())
    candidate = _candidate("m-no", yes_price=0.62, scan_score=0.40)
    research = _research_packet(
        "m-no",
        weighted_sentiment=-0.70,
        evidence_strength=0.80,
        disagreement_score=0.20,
    )

    result = agent.run(candidate, research)
    assert result.selected_side.value == "NO"
    assert result.fair_yes_prob == 0.4613
    assert result.selected_fair_price == 0.5387
    assert result.edge == 0.1587
    assert result.confidence == 0.5902
    assert any("selected_side=NO" in item for item in result.rationale)


def test_prediction_agent_is_repeatable_for_same_input() -> None:
    settings = PredictionSettings(market_weight=0.5, narrative_weight=0.4, structure_weight=0.1)
    agent = PredictionAgent(settings)
    candidate = _candidate("m-repeat", yes_price=0.51, scan_score=0.73)
    research = _research_packet(
        "m-repeat",
        weighted_sentiment=0.25,
        evidence_strength=0.65,
        disagreement_score=0.15,
    )

    first = agent.run(candidate, research)
    second = agent.run(candidate, research)
    assert first == second
