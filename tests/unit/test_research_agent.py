from __future__ import annotations

from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, ResearchFinding


def _candidate() -> MarketCandidate:
    snapshot = MarketSnapshot.from_yes_price(
        market_id="m-research",
        venue="polymarket",
        title="Will research metric be deterministic?",
        yes_price=0.53,
        liquidity_usd=25_000,
        volume_24h_usd=15_000,
        spread_bps=120,
        hours_to_resolution=12,
        last_price_move_bps=20,
        category="test",
    )
    return MarketCandidate(market=snapshot, scan_score=0.8, reasons=("seed",))


class _MockSource:
    def __init__(self, findings: list[ResearchFinding]) -> None:
        self._findings = findings

    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return list(self._findings)


class _FailingSource:
    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        raise RuntimeError(f"source unavailable for {market.market_id}")


def test_research_agent_builds_packet_with_expected_metrics() -> None:
    findings_a = [
        ResearchFinding(
            source_type=SourceType.REDDIT,
            source_name="reddit",
            summary="Community sees moderate upside.",
            sentiment=0.10,
            credibility=0.70,
        ),
        ResearchFinding(
            source_type=SourceType.TWITTER,
            source_name="x",
            summary="Strong bullish signal from event trackers.",
            sentiment=0.60,
            credibility=0.90,
        ),
    ]
    findings_b = [
        ResearchFinding(
            source_type=SourceType.RSS,
            source_name="rss",
            summary="Counterpoint from macro feed.",
            sentiment=-0.20,
            credibility=0.50,
        )
    ]

    agent = ResearchAgent([_MockSource(findings_b), _MockSource(findings_a)])
    packet = agent.run(_candidate())

    assert packet.market_id == "m-research"
    assert packet.weighted_sentiment == 0.2429
    assert packet.evidence_strength == 0.63
    assert packet.disagreement_score == 0.3289
    assert packet.narrative_summary == (
        "Strong bullish signal from event trackers.; "
        "Community sees moderate upside.; "
        "Counterpoint from macro feed."
    )


def test_research_agent_is_deterministic_for_input_order() -> None:
    finding_1 = ResearchFinding(
        source_type=SourceType.TWITTER,
        source_name="x",
        summary="Signal A",
        sentiment=0.4,
        credibility=0.8,
    )
    finding_2 = ResearchFinding(
        source_type=SourceType.RSS,
        source_name="rss",
        summary="Signal B",
        sentiment=-0.1,
        credibility=0.6,
    )

    agent_a = ResearchAgent([_MockSource([finding_1, finding_2])])
    agent_b = ResearchAgent([_MockSource([finding_2, finding_1])])

    packet_a = agent_a.run(_candidate())
    packet_b = agent_b.run(_candidate())

    assert packet_a.findings == packet_b.findings
    assert packet_a.weighted_sentiment == packet_b.weighted_sentiment
    assert packet_a.evidence_strength == packet_b.evidence_strength
    assert packet_a.disagreement_score == packet_b.disagreement_score
    assert packet_a.narrative_summary == packet_b.narrative_summary


def test_research_agent_returns_empty_packet_when_no_findings() -> None:
    agent = ResearchAgent([_MockSource([])])
    packet = agent.run(_candidate())

    assert packet.findings == ()
    assert packet.weighted_sentiment == 0.0
    assert packet.evidence_strength == 0.0
    assert packet.disagreement_score == 0.0
    assert packet.narrative_summary == "No evidence found."


def test_research_agent_tracks_source_failures_and_continues() -> None:
    finding = ResearchFinding(
        source_type=SourceType.RSS,
        source_name="rss",
        summary="Resilient signal",
        sentiment=0.2,
        credibility=0.8,
    )
    agent = ResearchAgent([_FailingSource(), _MockSource([finding])])

    packet = agent.run(_candidate())

    assert agent.last_source_failures == 1
    assert len(packet.findings) == 1
    assert packet.findings[0].summary == "Resilient signal"
