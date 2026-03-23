from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.agents.execution import DryRunExecutor, ExecutionAgent
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings, ScanSettings
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.infrastructure import JsonlPersistence, StaticMarketDataProvider
from prediction_market_bot.orchestration import PipelineCoordinator


class _NeutralSource:
    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="persistence-rss",
                summary=f"Neutral signal for {market.market_id}",
                sentiment=0.05,
                credibility=0.8,
            )
        ]


def _build_coordinator(base_dir: Path) -> PipelineCoordinator:
    artifacts_dir = base_dir / "artifacts"
    audit_path = base_dir / "audit" / "events.jsonl"
    persistence = JsonlPersistence(artifacts_dir=artifacts_dir, audit_log_path=audit_path)

    return PipelineCoordinator(
        market_data=StaticMarketDataProvider("polymarket"),
        scanner=ScanAgent(
            ScanSettings(
                min_liquidity_usd=1_000,
                min_volume_24h_usd=1_000,
                min_hours_to_resolution=1,
                max_spread_bps=400,
            )
        ),
        research=ResearchAgent([_NeutralSource()]),
        prediction=PredictionAgent(PredictionSettings(min_confidence=0.1, min_edge_bps=0)),
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
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_pipeline_persists_artifacts_and_events(tmp_path: Path) -> None:
    coordinator = _build_coordinator(tmp_path)
    summary = coordinator.run_dry(run_id="persist-run-1")

    artifacts_dir = tmp_path / "artifacts"
    audit_path = tmp_path / "audit" / "events.jsonl"

    market_snapshots = _read_jsonl(artifacts_dir / "market_snapshots.jsonl")
    research_packets = _read_jsonl(artifacts_dir / "research_packets.jsonl")
    prediction_results = _read_jsonl(artifacts_dir / "prediction_results.jsonl")
    risk_decisions = _read_jsonl(artifacts_dir / "risk_decisions.jsonl")
    execution_results = _read_jsonl(artifacts_dir / "execution_results.jsonl")
    settlement_results = _read_jsonl(artifacts_dir / "settlement_results.jsonl")
    postmortems = _read_jsonl(artifacts_dir / "postmortems.jsonl")
    pipeline_summaries = _read_jsonl(artifacts_dir / "pipeline_summaries.jsonl")
    events = _read_jsonl(audit_path)

    assert len(market_snapshots) == summary.total_markets
    assert len(research_packets) == summary.candidates
    assert len(prediction_results) == summary.candidates
    assert len(risk_decisions) == summary.candidates
    assert len(execution_results) == summary.candidates
    assert len(settlement_results) == summary.candidates
    assert len(postmortems) == summary.candidates
    assert len(pipeline_summaries) >= 1

    event_types = {item["event_type"] for item in events}
    assert "pipeline_start" in event_types
    assert "scan_start" in event_types
    assert "scan_end" in event_types
    assert "pipeline_end" in event_types

    # Spot-check payload shape for one artifact family.
    assert "payload" in prediction_results[0]
    assert "fair_yes_prob" in prediction_results[0]["payload"]  # type: ignore[index]

    summary_payload = pipeline_summaries[-1]["payload"]  # type: ignore[index]
    assert summary_payload["correlation_id"] == summary.run_id  # type: ignore[index]
    assert "counters" in summary_payload  # type: ignore[operator]
    assert "stage_timings_ms" in summary_payload  # type: ignore[operator]
