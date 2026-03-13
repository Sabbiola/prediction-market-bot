from prediction_market_bot.agents.execution import DryRunExecutor, ExecutionAgent
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.domain.enums import ExecutionStatus, OutcomeClassification, OutcomeSide, PostmortemCause, SourceType
from prediction_market_bot.domain.models import (
    ExecutionResult,
    PredictionResult,
    ResearchFinding,
    ResearchPacket,
    RiskDecision,
    SettlementResult,
)


def _prediction(
    *,
    market_id: str = "m-exec",
    side: OutcomeSide = OutcomeSide.YES,
    selected_market_price: float = 0.42,
    selected_fair_price: float = 0.58,
    confidence: float = 0.82,
) -> PredictionResult:
    market_yes_prob = selected_market_price if side is OutcomeSide.YES else 1.0 - selected_market_price
    fair_yes_prob = selected_fair_price if side is OutcomeSide.YES else 1.0 - selected_fair_price
    return PredictionResult(
        market_id=market_id,
        selected_side=side,
        market_yes_prob=round(market_yes_prob, 4),
        fair_yes_prob=round(fair_yes_prob, 4),
        selected_market_price=round(selected_market_price, 4),
        selected_fair_price=round(selected_fair_price, 4),
        edge=round(selected_fair_price - selected_market_price, 4),
        confidence=round(confidence, 4),
        rationale=("deterministic",),
    )


def _risk(
    *,
    market_id: str = "m-exec",
    approved: bool = True,
    side: OutcomeSide = OutcomeSide.YES,
    stake_usd: float = 120.0,
) -> RiskDecision:
    return RiskDecision(
        market_id=market_id,
        approved=approved,
        side=side,
        stake_usd=stake_usd if approved else 0.0,
        bankroll_fraction=0.012 if approved else 0.0,
        fractional_kelly=0.032,
        max_loss_usd=stake_usd if approved else 0.0,
        reasoning=("approved",) if approved else ("blocked",),
    )


def _research(*, market_id: str, evidence_strength: float, disagreement_score: float) -> ResearchPacket:
    return ResearchPacket(
        market_id=market_id,
        findings=(
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="rss",
                summary="mock finding",
                sentiment=0.1,
                credibility=0.8,
            ),
        ),
        weighted_sentiment=0.1,
        evidence_strength=evidence_strength,
        disagreement_score=disagreement_score,
        narrative_summary="mock",
    )


def test_execution_agent_simulates_order_creation_and_fill_deterministically() -> None:
    agent = ExecutionAgent(venue="polymarket", executor=DryRunExecutor())
    risk = _risk(approved=True, side=OutcomeSide.YES, stake_usd=120.0)
    prediction = _prediction(side=OutcomeSide.YES, selected_market_price=0.42, selected_fair_price=0.58)

    first = agent.run(risk, prediction)
    second = agent.run(risk, prediction)

    assert first == second
    assert first.status is ExecutionStatus.FILLED
    assert first.order_id is not None
    assert first.order_id.startswith("dryrun-m-exec-yes-")
    assert first.fill_price is not None
    assert first.fill_price >= prediction.selected_market_price
    assert "order_created_and_filled" in first.message


def test_execution_agent_skips_when_risk_not_approved() -> None:
    agent = ExecutionAgent(venue="polymarket", executor=DryRunExecutor())
    result = agent.run(_risk(approved=False), _prediction())

    assert result.status is ExecutionStatus.SKIPPED
    assert result.stake_usd == 0.0
    assert result.order_id is None


def test_settlement_agent_simulates_pnl_and_classification() -> None:
    settlement = SettlementAgent()

    filled = ExecutionResult(
        market_id="m-settle",
        status=ExecutionStatus.FILLED,
        side=OutcomeSide.YES,
        stake_usd=100.0,
        fill_price=0.4,
        order_id="dryrun-m-settle-yes-10000",
        message="ok",
    )

    win = settlement.settle(filled, resolved_yes=True)
    loss = settlement.settle(filled, resolved_yes=False)
    skipped = settlement.settle(
        ExecutionResult(
            market_id="m-settle",
            status=ExecutionStatus.SKIPPED,
            side=OutcomeSide.YES,
            stake_usd=0.0,
            message="skipped",
        ),
        resolved_yes=False,
    )

    assert win.outcome_classification is OutcomeClassification.WIN
    assert win.pnl_usd == 150.0
    assert loss.outcome_classification is OutcomeClassification.LOSS
    assert loss.pnl_usd == -100.0
    assert skipped.outcome_classification is OutcomeClassification.SKIPPED
    assert skipped.pnl_usd == 0.0


def test_postmortem_agent_generates_root_causes_and_actions() -> None:
    agent = PostmortemAgent()
    settlement = SettlementResult(
        market_id="m-loss",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        avg_price=0.8,
        resolved_yes=False,
        pnl_usd=-100.0,
        outcome_classification=OutcomeClassification.LOSS,
    )
    prediction = _prediction(
        market_id="m-loss",
        side=OutcomeSide.YES,
        selected_market_price=0.8,
        selected_fair_price=0.9,
        confidence=0.9,
    )
    research = _research(market_id="m-loss", evidence_strength=0.2, disagreement_score=0.7)

    report = agent.run(settlement, prediction, research)
    assert report.outcome_classification is OutcomeClassification.LOSS
    assert PostmortemCause.DATA_GAP in report.causes
    assert PostmortemCause.RESEARCH_NOISE in report.causes
    assert PostmortemCause.CALIBRATION_ERROR in report.causes
    assert PostmortemCause.LIQUIDITY_TRAP in report.causes
    assert len(report.action_items) >= 3
    assert "Outcome=LOSS" in report.summary


def test_postmortem_agent_handles_win_without_root_causes() -> None:
    agent = PostmortemAgent()
    settlement = SettlementResult(
        market_id="m-win",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        avg_price=0.4,
        resolved_yes=True,
        pnl_usd=150.0,
        outcome_classification=OutcomeClassification.WIN,
    )
    report = agent.run(
        settlement,
        _prediction(market_id="m-win"),
        _research(market_id="m-win", evidence_strength=0.7, disagreement_score=0.1),
    )

    assert report.outcome_classification is OutcomeClassification.WIN
    assert report.causes == ()
    assert len(report.action_items) >= 1
