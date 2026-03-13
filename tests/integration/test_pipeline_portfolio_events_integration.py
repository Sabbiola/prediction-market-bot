from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.agents.execution import DryRunExecutor, ExecutionAgent
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings, ScanSettings
from prediction_market_bot.domain.enums import OutcomeSide, SourceType
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult, ResearchFinding, ResearchPacket
from prediction_market_bot.infrastructure import JsonlPersistence, StaticMarketDataProvider
from prediction_market_bot.orchestration import PipelineCoordinator
from prediction_market_bot.services import PaperPortfolioEngine


class _Source:
    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="portfolio-rss",
                summary=f"Portfolio event source for {market.market_id}",
                sentiment=0.2,
                credibility=0.8,
            )
        ]


class _PositivePredictionAgent:
    name = "positive-prediction-agent"

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        del research
        market_price = candidate.market.yes_price
        fair_price = min(market_price + 0.2, 0.99)
        return PredictionResult(
            market_id=candidate.market.market_id,
            selected_side=OutcomeSide.YES,
            market_yes_prob=market_price,
            fair_yes_prob=fair_price,
            selected_market_price=market_price,
            selected_fair_price=fair_price,
            edge=round(fair_price - market_price, 4),
            confidence=0.85,
            rationale=("positive-test-signal",),
        )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_pipeline_persists_paper_portfolio_events(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    audit_path = tmp_path / "audit" / "events.jsonl"
    persistence = JsonlPersistence(artifacts_dir=artifacts_dir, audit_log_path=audit_path)
    coordinator = PipelineCoordinator(
        market_data=StaticMarketDataProvider("polymarket"),
        scanner=ScanAgent(
            ScanSettings(
                min_liquidity_usd=1_000,
                min_volume_24h_usd=1_000,
                min_hours_to_resolution=1,
                max_spread_bps=400,
            )
        ),
        research=ResearchAgent([_Source()]),
        prediction=_PositivePredictionAgent(),
        risk=RiskAgent(
            RiskSettings(
                bankroll_usd=10_000.0,
                fractional_kelly=0.25,
                max_position_pct=0.02,
                max_event_bucket_pct=0.08,
                max_category_bucket_pct=0.10,
                max_daily_loss_pct=0.05,
                min_bet_usd=1.0,
            ),
            PredictionSettings(min_confidence=0.1, min_edge_bps=0),
        ),
        execution=ExecutionAgent("polymarket", DryRunExecutor()),
        settlement=SettlementAgent(),
        postmortem=PostmortemAgent(),
        persistence=persistence,
        portfolio=PaperPortfolioEngine(persistence=persistence),
    )

    summary = coordinator.run_dry(run_id="portfolio-persist-run-1")
    assert summary.candidates >= 1

    portfolio_events = _read_jsonl(artifacts_dir / "paper_portfolio_events.jsonl")
    assert len(portfolio_events) >= summary.candidates
    payloads = [row.get("payload") for row in portfolio_events if row.get("run_id") == "portfolio-persist-run-1"]
    event_types = {
        payload["event_type"]  # type: ignore[index]
        for payload in payloads
        if isinstance(payload, dict) and "event_type" in payload
    }
    assert "fill" in event_types
    assert "settle" in event_types
