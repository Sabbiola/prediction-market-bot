from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from prediction_market_bot.domain.enums import ExecutionStatus, MarketStatus, OutcomeSide, PostmortemCause, SourceType, TxStatus
from prediction_market_bot.domain.models import (
    ExecutionResult,
    Market,
    MarketSnapshot,
    NonceState,
    OutcomeQuote,
    OrderIntent,
    PostmortemReport,
    PredictionResult,
    ResearchFinding,
    ResearchPacket,
    RiskDecision,
    SettlementResult,
    TxAttempt,
    TxIntent,
    TxReceipt,
)


def test_market_snapshot_valid_and_serializable() -> None:
    snapshot = MarketSnapshot(
        market=Market(
            market_id="m-1",
            venue="polymarket",
            title="Will x happen?",
            category="macro",
            status=MarketStatus.OPEN,
            updated_at=datetime.now(UTC),
        ),
        outcome_quotes=(
            OutcomeQuote(side=OutcomeSide.YES, price=0.41, liquidity_usd=10_000),
            OutcomeQuote(side=OutcomeSide.NO, price=0.59, liquidity_usd=10_000),
        ),
        liquidity_usd=10_000,
        volume_24h_usd=5_000,
        spread_bps=120,
        hours_to_resolution=24,
        last_price_move_bps=100,
    )

    payload = snapshot.model_dump()
    restored = MarketSnapshot.model_validate(payload)
    assert restored.market_id == "m-1"
    assert restored.yes_price == 0.41
    assert restored.no_price == 0.59


def test_market_snapshot_rejects_non_strict_values() -> None:
    with pytest.raises(ValidationError):
        OutcomeQuote(side=OutcomeSide.YES, price="0.41", liquidity_usd=10_000)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        MarketSnapshot.from_yes_price(
            market_id="m-1",
            venue="polymarket",
            title="Will x happen?",
            yes_price=0.41,
            liquidity_usd=10_000,
            volume_24h_usd=5_000,
            spread_bps="120",  # type: ignore[arg-type]
            hours_to_resolution=24,
            last_price_move_bps=100,
        )


def test_market_snapshot_requires_both_yes_and_no_quotes() -> None:
    with pytest.raises(ValidationError):
        MarketSnapshot(
            market=Market(
                market_id="m-2",
                venue="polymarket",
                title="Will y happen?",
                category="macro",
                status=MarketStatus.OPEN,
            ),
            outcome_quotes=(OutcomeQuote(side=OutcomeSide.YES, price=0.9),),
            liquidity_usd=10_000,
            volume_24h_usd=5_000,
            spread_bps=80,
            hours_to_resolution=48,
            last_price_move_bps=50,
        )


def test_research_models_validate_ranges() -> None:
    with pytest.raises(ValidationError):
        ResearchFinding(
            source_type=SourceType.RSS,
            source_name="rss",
            summary="bad",
            sentiment=2.0,
            credibility=0.5,
        )

    packet = ResearchPacket(
        market_id="m-3",
        findings=(
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="rss",
                summary="ok",
                sentiment=0.2,
                credibility=0.8,
            ),
        ),
        weighted_sentiment=0.2,
        evidence_strength=0.8,
        disagreement_score=0.1,
        narrative_summary="ok",
    )
    assert packet.findings[0].source_type is SourceType.RSS


def test_prediction_risk_execution_settlement_postmortem_roundtrip() -> None:
    prediction = PredictionResult(
        market_id="m-4",
        selected_side=OutcomeSide.YES,
        market_yes_prob=0.4,
        fair_yes_prob=0.55,
        selected_market_price=0.4,
        selected_fair_price=0.55,
        edge=0.15,
        confidence=0.8,
        rationale=("demo",),
    )
    risk = RiskDecision(
        market_id="m-4",
        approved=True,
        side=OutcomeSide.YES,
        stake_usd=100.0,
        bankroll_fraction=0.01,
        fractional_kelly=0.02,
        max_loss_usd=100.0,
        reasoning=("approved",),
    )
    order = OrderIntent(
        market_id="m-4",
        venue="polymarket",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        limit_price=0.4,
        rationale="demo",
    )
    execution = ExecutionResult(
        market_id="m-4",
        status=ExecutionStatus.SUBMITTED,
        side=OutcomeSide.YES,
        stake_usd=100.0,
        fill_price=0.4,
        order_id="o-1",
        message="ok",
    )
    settlement = SettlementResult(
        market_id="m-4",
        side=OutcomeSide.YES,
        stake_usd=100.0,
        avg_price=0.4,
        resolved_yes=True,
        pnl_usd=150.0,
    )
    postmortem = PostmortemReport(
        market_id="m-4",
        causes=(PostmortemCause.CALIBRATION_ERROR,),
        summary="analysis",
        action_items=("review calibration",),
    )

    assert PredictionResult.model_validate_json(prediction.model_dump_json()) == prediction
    assert RiskDecision.model_validate_json(risk.model_dump_json()) == risk
    assert OrderIntent.model_validate_json(order.model_dump_json()) == order
    assert ExecutionResult.model_validate_json(execution.model_dump_json()) == execution
    assert SettlementResult.model_validate_json(settlement.model_dump_json()) == settlement
    assert PostmortemReport.model_validate_json(postmortem.model_dump_json()) == postmortem


def test_transaction_plane_aliases_and_nonce_state_roundtrip() -> None:
    intent = TxIntent(
        market_id="m-tx",
        venue="polymarket",
        side=OutcomeSide.NO,
        stake_usd=42.0,
        limit_price=0.61,
        rationale="tx-plane",
    )
    receipt = TxReceipt(
        market_id="m-tx",
        status=TxStatus.SUBMITTED,
        side=OutcomeSide.NO,
        stake_usd=42.0,
        fill_price=None,
        order_id="tx-1",
        message="submitted",
    )
    attempt = TxAttempt(
        market_id="m-tx",
        execution_mode=receipt.execution_mode,
        lane="main",
        intent_id="intent-1",
        venue=intent.venue,
        side=intent.side,
        stake_usd=intent.stake_usd,
        limit_price=intent.limit_price,
        status=TxStatus.SUBMITTED,
    )
    nonce_state = NonceState(
        chain_id=80_002,
        account="0xabc",
        next_nonce=7,
    )

    assert isinstance(intent, OrderIntent)
    assert isinstance(receipt, ExecutionResult)
    assert TxIntent.model_validate_json(intent.model_dump_json()) == intent
    assert TxReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    assert TxAttempt.model_validate_json(attempt.model_dump_json()) == attempt
    assert NonceState.model_validate_json(nonce_state.model_dump_json()) == nonce_state
