from __future__ import annotations

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
from prediction_market_bot.services import PaperPortfolioEngine, TradeReviewQueueService


class _Source:
    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="review-rss",
                summary=f"Review source for {market.market_id}",
                sentiment=0.3,
                credibility=0.8,
            )
        ]


class _PositivePredictionAgent:
    name = "positive-prediction-agent"

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> PredictionResult:
        del research
        market_yes = candidate.market.yes_price
        fair_yes = min(market_yes + 0.25, 0.99)
        return PredictionResult(
            market_id=candidate.market.market_id,
            selected_side=OutcomeSide.YES,
            market_yes_prob=market_yes,
            fair_yes_prob=fair_yes,
            selected_market_price=market_yes,
            selected_fair_price=fair_yes,
            edge=round(fair_yes - market_yes, 4),
            confidence=0.9,
            rationale=("model_positive_signal", "macro_alignment"),
        )


def test_pipeline_enqueues_trade_review_candidates_for_approved_trades(tmp_path: Path) -> None:
    persistence = JsonlPersistence(
        artifacts_dir=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit" / "events.jsonl",
    )
    queue = TradeReviewQueueService(persistence)
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
        review_queue_hook=queue.enqueue_candidate,
    )

    summary = coordinator.run_dry(run_id="review-queue-run-1")
    assert summary.candidates >= 1

    queued_rows = persistence.read_artifact_records("review-queue-run-1", "trade_review_candidates")
    assert len(queued_rows) >= 1
    payload = queued_rows[0]["payload"]  # type: ignore[index]
    assert payload["queue_id"]  # type: ignore[index]
    assert payload["model_rationale"]  # type: ignore[index]

    events = persistence.read_run_events("review-queue-run-1")
    event_types = {row.get("event_type") for row in events}
    assert "trade_review_queued" in event_types
