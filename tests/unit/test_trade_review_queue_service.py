from __future__ import annotations

from pathlib import Path

from prediction_market_bot.domain.enums import OutcomeSide, TradeReviewAction, TradeReviewStatus
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult, RiskDecision
from prediction_market_bot.infrastructure import JsonlPersistence
from prediction_market_bot.services import TradeReviewQueueService


def _candidate() -> MarketCandidate:
    snapshot = MarketSnapshot.from_yes_price(
        market_id="review-market-1",
        venue="polymarket",
        title="Will review queue capture approved candidates?",
        yes_price=0.41,
        liquidity_usd=55_000,
        volume_24h_usd=30_000,
        spread_bps=90,
        hours_to_resolution=24,
        last_price_move_bps=15,
        category="test",
    )
    return MarketCandidate(market=snapshot, scan_score=0.82, reasons=("test",))


def _prediction() -> PredictionResult:
    return PredictionResult(
        market_id="review-market-1",
        selected_side=OutcomeSide.YES,
        market_yes_prob=0.41,
        fair_yes_prob=0.58,
        selected_market_price=0.41,
        selected_fair_price=0.58,
        edge=0.17,
        confidence=0.81,
        rationale=("pred-r1", "pred-r2"),
    )


def _approved_risk() -> RiskDecision:
    return RiskDecision(
        market_id="review-market-1",
        approved=True,
        side=OutcomeSide.YES,
        stake_usd=120.0,
        bankroll_fraction=0.012,
        fractional_kelly=0.026,
        max_loss_usd=120.0,
        reasoning=("risk-r1", "risk-r2"),
    )


def _service(tmp_path: Path) -> tuple[TradeReviewQueueService, JsonlPersistence]:
    persistence = JsonlPersistence(
        artifacts_dir=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit" / "events.jsonl",
    )
    return TradeReviewQueueService(persistence), persistence


def test_trade_review_queue_approval_and_note_workflow(tmp_path: Path) -> None:
    queue, persistence = _service(tmp_path)
    queued = queue.enqueue_candidate("review-run-1", _candidate(), _prediction(), _approved_risk())

    pending = queue.list_queue(status=TradeReviewStatus.PENDING, limit=10)
    assert len(pending) == 1
    assert pending[0].queue_id == queued.queue_id
    assert pending[0].model_rationale == ("pred-r1", "pred-r2", "risk-r1", "risk-r2")

    approved = queue.apply_action(
        queue_id=queued.queue_id,
        action=TradeReviewAction.APPROVE,
        operator_id="alice",
        operator_rationale="Looks aligned with policy.",
        note="Proceed for paper review.",
    )
    assert approved.status == TradeReviewStatus.APPROVED
    assert approved.operator_rationale == "Looks aligned with policy."

    noted = queue.apply_action(
        queue_id=queued.queue_id,
        action=TradeReviewAction.NOTE,
        operator_id="alice",
        operator_rationale="Adding context note",
        note="Monitor macro surprise risk",
    )
    assert noted.status == TradeReviewStatus.APPROVED
    assert "alice: Monitor macro surprise risk" in noted.notes

    decision_rows = persistence.read_artifact_records("review-run-1", "trade_review_decisions")
    assert len(decision_rows) == 2


def test_trade_review_queue_reject_action_updates_status(tmp_path: Path) -> None:
    queue, _ = _service(tmp_path)
    queued = queue.enqueue_candidate("review-run-2", _candidate(), _prediction(), _approved_risk())

    rejected = queue.apply_action(
        queue_id=queued.queue_id,
        action=TradeReviewAction.REJECT,
        operator_id="bob",
        operator_rationale="Insufficient catalyst evidence.",
    )
    assert rejected.status == TradeReviewStatus.REJECTED
    assert rejected.operator_rationale == "Insufficient catalyst evidence."
