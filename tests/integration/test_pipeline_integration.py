from __future__ import annotations

import logging

import pytest

from prediction_market_bot.agents.execution import DryRunExecutor, ExecutionAgent
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.settings import PredictionSettings, RiskSettings, ScanSettings
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, ResearchFinding, ResearchPacket
from prediction_market_bot.infrastructure import StaticMarketDataProvider
from prediction_market_bot.orchestration import PipelineCoordinator


class _BullishSource:
    def fetch(self, market: MarketSnapshot) -> list[ResearchFinding]:
        return [
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="integration-rss",
                summary=f"Bullish signal for {market.market_id}",
                sentiment=0.5,
                credibility=0.9,
            )
        ]


class _FailingPredictionAgent:
    name = "failing-prediction-agent"

    def run(self, candidate: MarketCandidate, research: ResearchPacket) -> object:
        del candidate, research
        raise RuntimeError("prediction stage exploded")


def _build_coordinator() -> PipelineCoordinator:
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
        research=ResearchAgent([_BullishSource()]),
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
    )


def test_pipeline_returns_summary_object_with_records() -> None:
    coordinator = _build_coordinator()
    summary = coordinator.run_dry(run_id="integration-run-1")

    assert summary.run_id == "integration-run-1"
    assert summary.total_markets >= 1
    assert summary.candidates >= 1
    assert summary.settled_count == len(summary.records)
    assert summary.executed_count <= summary.settled_count
    assert summary.wins + summary.losses + summary.skipped <= summary.settled_count

    first = summary.records[0]
    assert first.market_id == first.prediction.market_id
    assert first.settlement.market_id == first.market_id
    assert first.postmortem.market_id == first.market_id


def test_pipeline_emits_structured_logs_per_stage(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="prediction_market_bot.orchestration.coordinator")
    coordinator = _build_coordinator()
    coordinator.run_dry(run_id="integration-run-2")

    events = [record.__dict__.get("event") for record in caplog.records]
    expected_events = {
        "scan_start",
        "scan_end",
        "research_end",
        "prediction_end",
        "risk_end",
        "execution_end",
        "settlement_end",
        "postmortem_end",
        "pipeline_end",
    }
    for event in expected_events:
        assert event in events


def test_pipeline_triggers_alert_hook_on_critical_failure() -> None:
    alerts: list[tuple[str, dict[str, object]]] = []

    def _alert_hook(event_type: str, payload: dict[str, object]) -> None:
        alerts.append((event_type, dict(payload)))

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
        research=ResearchAgent([_BullishSource()]),
        prediction=_FailingPredictionAgent(),
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
        alert_hook=_alert_hook,
    )

    with pytest.raises(RuntimeError, match="prediction stage exploded"):
        coordinator.run_dry(run_id="integration-critical-1")

    assert len(alerts) == 1
    event_type, payload = alerts[0]
    assert event_type == "pipeline_critical_failure"
    assert payload["run_id"] == "integration-critical-1"
    assert payload["correlation_id"] == "integration-critical-1"
    assert payload["failed_stage"] == "prediction"
