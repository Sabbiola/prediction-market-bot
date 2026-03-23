from __future__ import annotations

from pathlib import Path

from prediction_market_bot.agents.execution import (
    ExecutionAgent,
    LiveDisabledExecutor,
    PaperExecutor,
    ShadowSignExecutor,
)
from prediction_market_bot.app.bootstrap import _build_executor_for_mode, build_http_client
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import ExecutionMode, OutcomeSide, TxStatus
from prediction_market_bot.domain.models import PredictionResult, RiskDecision, TxAttempt, TxIntent, TxReceipt
from prediction_market_bot.infrastructure import SandboxChainExecutor


def _prediction(*, market_id: str = "m-tx", side: OutcomeSide = OutcomeSide.YES) -> PredictionResult:
    return PredictionResult(
        market_id=market_id,
        selected_side=side,
        market_yes_prob=0.45 if side is OutcomeSide.YES else 0.55,
        fair_yes_prob=0.55 if side is OutcomeSide.YES else 0.45,
        selected_market_price=0.45,
        selected_fair_price=0.55,
        edge=0.1,
        confidence=0.8,
        rationale=("tx-plane",),
    )


def _risk(*, market_id: str = "m-tx", side: OutcomeSide = OutcomeSide.YES) -> RiskDecision:
    return RiskDecision(
        market_id=market_id,
        approved=True,
        side=side,
        stake_usd=125.0,
        bankroll_fraction=0.01,
        fractional_kelly=0.02,
        max_loss_usd=125.0,
        reasoning=("approved",),
    )


def test_executor_factory_selects_explicit_execution_modes(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    http_client = build_http_client(settings)

    paper = _build_executor_for_mode(mode=ExecutionMode.PAPER, settings=settings, http_client=http_client)
    shadow = _build_executor_for_mode(mode=ExecutionMode.SHADOW_SIGN, settings=settings, http_client=http_client)
    sandbox = _build_executor_for_mode(mode=ExecutionMode.SANDBOX_CHAIN, settings=settings, http_client=http_client)
    disabled = _build_executor_for_mode(mode=ExecutionMode.LIVE_DISABLED, settings=settings, http_client=http_client)

    assert isinstance(paper, PaperExecutor)
    assert isinstance(shadow, ShadowSignExecutor)
    assert isinstance(sandbox, SandboxChainExecutor)
    assert isinstance(disabled, LiveDisabledExecutor)


def test_transaction_plane_lifecycle_is_consistent_across_lanes() -> None:
    main_executor = PaperExecutor()
    rehearsal_executor = ShadowSignExecutor()
    agent = ExecutionAgent(
        venue="polymarket",
        executor=main_executor,
        execution_mode=ExecutionMode.PAPER,
        rehearsal_executor=rehearsal_executor,
        rehearsal_mode=ExecutionMode.SHADOW_SIGN,
    )
    risk = _risk()
    prediction = _prediction()

    intent = agent.build_order_intent(risk, prediction)
    receipt = agent.run(risk, prediction)

    assert isinstance(intent, TxIntent)
    assert isinstance(receipt, TxReceipt)
    assert receipt.status is TxStatus.FILLED
    assert len(agent.last_attempts) == 2

    main_attempt, rehearsal_attempt = agent.last_attempts
    assert isinstance(main_attempt, TxAttempt)
    assert isinstance(rehearsal_attempt, TxAttempt)
    assert main_attempt.lane == "main"
    assert rehearsal_attempt.lane == "rehearsal"
    assert main_attempt.status is TxStatus.FILLED
    assert rehearsal_attempt.status is TxStatus.SUBMITTED
