from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, TxConfirmationStatus
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, PredictionResult, RiskDecision, TxAttempt, TxIntent
from prediction_market_bot.services import TradeReviewQueueService
from prediction_market_bot.ui.app import create_web_app


def _seed_review_candidate(queue: TradeReviewQueueService, run_id: str, market_id: str) -> str:
    candidate = MarketCandidate(
        market=MarketSnapshot.from_yes_price(
            market_id=market_id,
            venue="polymarket",
            title="Will operator UI review path work?",
            yes_price=0.42,
            liquidity_usd=60_000,
            volume_24h_usd=32_000,
            spread_bps=90,
            hours_to_resolution=36,
            last_price_move_bps=12,
            category="test",
        ),
        scan_score=0.84,
        reasons=("seed",),
    )
    prediction = PredictionResult(
        market_id=market_id,
        selected_side=OutcomeSide.YES,
        market_yes_prob=0.42,
        fair_yes_prob=0.63,
        selected_market_price=0.42,
        selected_fair_price=0.63,
        edge=0.21,
        confidence=0.87,
        rationale=("ui_review_prediction_rationale",),
    )
    risk = RiskDecision(
        market_id=market_id,
        approved=True,
        side=OutcomeSide.YES,
        stake_usd=110.0,
        bankroll_fraction=0.011,
        fractional_kelly=0.023,
        max_loss_usd=110.0,
        reasoning=("ui_review_risk_rationale",),
    )
    queued = queue.enqueue_candidate(run_id, candidate, prediction, risk)
    return queued.queue_id


def _set_sandbox_chain_enabled(app_cfg: Path, *, submit_tx: bool) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    sandbox = raw.setdefault("sandbox_chain", {})
    if not isinstance(sandbox, dict):
        raise AssertionError("sandbox_chain section must be a mapping")
    sandbox["enabled"] = True
    sandbox["submit_tx"] = submit_tx
    sandbox["rpc_url"] = "https://sandbox-chain.example/rpc"
    sandbox["contract_address"] = "0x1234567890123456789012345678901234567890"
    sandbox["from_address"] = "0x1111111111111111111111111111111111111111"
    sandbox["allow_unlocked_send"] = True
    sandbox["private_key_env"] = "SANDBOX_CHAIN_PRIVATE_KEY"
    raw["sandbox_chain"] = sandbox
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_ui_review_and_sandbox_tx_actions_preserve_audit_trail(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_sandbox_chain_enabled(app_cfg, submit_tx=True)

    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)

    review_queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    run_id = f"{deterministic_run_id}-ui-review-tx"
    queue_id = _seed_review_candidate(review_queue, run_id, market_id="ui-review-market-1")

    intent = TxIntent(
        market_id="ui-sandbox-market-1",
        venue="polymarket",
        side=OutcomeSide.YES,
        stake_usd=125.0,
        limit_price=0.51,
        rationale="ui_sandbox_rationale",
        run_id=run_id,
        review_queue_id=queue_id,
    )
    operational.transaction_intents.upsert_intent(run_id, intent)

    status_seed = TxAttempt(
        market_id=intent.market_id,
        execution_mode=ExecutionMode.SANDBOX_CHAIN,
        lane="sandbox_chain",
        intent_id=_intent_id(intent),
        venue=intent.venue,
        side=intent.side,
        stake_usd=intent.stake_usd,
        limit_price=intent.limit_price,
        status=ExecutionStatus.SUBMITTED,
        tx_hash="",
        nonce=7,
        chain_id=80_002,
        from_address="0x1111111111111111111111111111111111111111",
        run_id=run_id,
        review_queue_id=queue_id,
        confirmation_status=TxConfirmationStatus.PENDING,
        message="seed_pending_for_ui",
    )
    operational.transaction_attempts.append_attempt(run_id, status_seed)

    app = create_web_app(config_path=app_cfg, agents_config_path=agents_cfg)

    with TestClient(app) as client:
        review_tab = client.get(f"/api/tabs/review-queue?run_id={run_id}&status=PENDING_REVIEW")
        assert review_tab.status_code == 200
        review_payload = review_tab.json()
        assert review_payload["rows"]
        assert review_payload["selected_item"]["model_rationale"]

        approve = client.post(
            "/api/actions/review-approve",
            json={
                "queue_id": queue_id,
                "operator_id": "ui-operator-1",
                "rationale": "Approved from UI after manual checks.",
                "note": "ui approval note",
                "confirm": True,
            },
        )
        assert approve.status_code == 200
        approve_payload = approve.json()
        assert approve_payload["status"] == "completed"
        assert approve_payload["accepted"] is True

        blocked_review = client.post(
            "/api/actions/review-reject",
            json={
                "queue_id": queue_id,
                "operator_id": "ui-operator-1",
                "rationale": "This should be blocked because confirmation is missing.",
                "note": "",
                "confirm": False,
            },
        )
        assert blocked_review.status_code == 200
        assert blocked_review.json()["status"] == "blocked"

        approved_tab = client.get(
            f"/api/tabs/review-queue?run_id={run_id}&status=APPROVED&queue_id={queue_id}"
        )
        assert approved_tab.status_code == 200
        approved_payload = approved_tab.json()
        assert approved_payload["selected_item"]["status"] == "APPROVED"
        assert approved_payload["selected_item"]["operator_rationale"] == "Approved from UI after manual checks."

        sandbox_tab = client.get(f"/api/tabs/sandbox-tx?run_id={run_id}&intent_id={_intent_id(intent)}")
        assert sandbox_tab.status_code == 200
        sandbox_payload = sandbox_tab.json()
        assert sandbox_payload["selected_intent"]["intent_id"] == _intent_id(intent)
        assert sandbox_payload["attempt_timeline"]

        reconcile = client.post(
            "/api/actions/tx-reconcile",
            json={"run_id": run_id, "intent_id": _intent_id(intent), "limit": 5},
        )
        assert reconcile.status_code == 200
        reconcile_payload = reconcile.json()
        assert reconcile_payload["accepted"] is True
        assert reconcile_payload["status"] == "completed"

        blocked_resubmit = client.post(
            "/api/actions/tx-resubmit-safe",
            json={"intent_id": _intent_id(intent), "confirm": False},
        )
        assert blocked_resubmit.status_code == 200
        blocked_resubmit_payload = blocked_resubmit.json()
        assert blocked_resubmit_payload["status"] == "blocked"

    audit_rows = persistence.read_all_artifact_records("ui_operator_actions")
    actions = [
        row.get("payload", {}).get("action")
        for row in audit_rows
        if isinstance(row.get("payload"), dict)
    ]
    assert "review-approve" in actions
    assert "review-reject" in actions
    assert "tx-reconcile" in actions
    assert "tx-resubmit-safe" in actions

    for row in audit_rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        assert "private_key" not in payload


def _intent_id(intent: TxIntent) -> str:
    import hashlib

    payload = (
        f"{intent.run_id}|{intent.review_queue_id}|{intent.market_id}|{intent.venue}|{intent.side.value}|"
        f"{intent.stake_usd:.6f}|{intent.limit_price:.6f}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
