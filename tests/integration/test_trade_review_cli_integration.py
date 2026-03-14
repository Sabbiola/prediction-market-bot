from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult, RiskDecision
from prediction_market_bot.main import build_persistence, main
from prediction_market_bot.services import TradeReviewQueueService


def _seed_review_candidate(
    *,
    queue: TradeReviewQueueService,
    run_id: str,
    market_id: str = "review-cli-market-1",
) -> str:
    candidate = MarketCandidate(
        market=MarketSnapshot.from_yes_price(
            market_id=market_id,
            venue="polymarket",
            title="Will CLI review workflow persist decisions?",
            yes_price=0.39,
            liquidity_usd=40_000,
            volume_24h_usd=20_000,
            spread_bps=120,
            hours_to_resolution=48,
            last_price_move_bps=10,
            category="test",
        ),
        scan_score=0.8,
        reasons=("seed",),
    )
    prediction = PredictionResult(
        market_id=market_id,
        selected_side=OutcomeSide.YES,
        market_yes_prob=0.39,
        fair_yes_prob=0.62,
        selected_market_price=0.39,
        selected_fair_price=0.62,
        edge=0.23,
        confidence=0.86,
        rationale=("prediction_strength", "evidence_alignment"),
    )
    risk = RiskDecision(
        market_id=market_id,
        approved=True,
        side=OutcomeSide.YES,
        stake_usd=150.0,
        bankroll_fraction=0.015,
        fractional_kelly=0.03,
        max_loss_usd=150.0,
        reasoning=("risk_check_passed",),
    )
    queued = queue.enqueue_candidate(run_id, candidate, prediction, risk)
    return queued.queue_id


def test_cli_review_queue_and_actions_workflow(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)
    queue = TradeReviewQueueService(persistence)
    queue_id = _seed_review_candidate(queue=queue, run_id=deterministic_run_id, market_id="review-cli-market-1")
    queue_id_2 = _seed_review_candidate(queue=queue, run_id=deterministic_run_id, market_id="review-cli-market-2")

    list_exit = main(
        [
            "review-list",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--status",
            "pending_review",
            "--json",
        ]
    )
    assert list_exit == 0
    pending_items = json.loads(capsys.readouterr().out)
    assert len(pending_items) == 2
    assert {item["queue_id"] for item in pending_items} == {queue_id, queue_id_2}
    assert all(item["model_rationale"] for item in pending_items)

    show_exit = main(
        [
            "review-show",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            queue_id,
            "--json",
        ]
    )
    assert show_exit == 0
    shown_item = json.loads(capsys.readouterr().out)
    assert shown_item["queue_id"] == queue_id
    assert shown_item["status"] == "PENDING_REVIEW"

    approve_exit = main(
        [
            "review-approve",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            queue_id,
            "--operator-id",
            "operator-a",
            "--rationale",
            "Approved after manual check.",
        ]
    )
    assert approve_exit == 0
    approved_payload = json.loads(capsys.readouterr().out)
    assert approved_payload["status"] == "APPROVED"
    assert approved_payload["operator_rationale"] == "Approved after manual check."

    reject_exit = main(
        [
            "review-reject",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            queue_id_2,
            "--operator-id",
            "operator-b",
            "--rationale",
            "Rejected after manual check.",
        ]
    )
    assert reject_exit == 0
    rejected_payload = json.loads(capsys.readouterr().out)
    assert rejected_payload["status"] == "REJECTED"
    assert rejected_payload["operator_rationale"] == "Rejected after manual check."

    note_exit = main(
        [
            "review-action",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            queue_id,
            "--action",
            "note",
            "--operator-id",
            "operator-a",
            "--rationale",
            "Post-approval note.",
            "--note",
            "Monitor volatility regime changes.",
        ]
    )
    assert note_exit == 0
    note_payload = json.loads(capsys.readouterr().out)
    assert note_payload["status"] == "APPROVED"
    assert any("Monitor volatility regime changes." in note for note in note_payload["notes"])

    approved_list_exit = main(
        [
            "review-list",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
            "--status",
            "approved",
            "--json",
        ]
    )
    assert approved_list_exit == 0
    approved_items = json.loads(capsys.readouterr().out)
    assert len(approved_items) == 1
    assert approved_items[0]["queue_id"] == queue_id

    decision_rows = persistence.read_artifact_records(deterministic_run_id, "trade_review_decisions")
    assert len(decision_rows) == 3
