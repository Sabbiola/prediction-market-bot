from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.strategy_research.benchmarking import BaselinePrediction, LabelRecord
from prediction_market_bot.strategy_research.simulator import (
    SimulationAssumptions,
    SimulationThresholds,
    WalkForwardStrategyService,
)


def _label(
    idx: int,
    *,
    decision_days: int,
    label_yes: int,
    yes_price: float = 0.5,
) -> LabelRecord:
    decision_ts = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=decision_days)
    return LabelRecord(
        row_id=f"m-{idx}@{decision_ts.isoformat()}",
        market_id=f"m-{idx}",
        event_id=f"e-{idx}",
        category="macro",
        market_title=f"market-{idx}",
        decision_timestamp_utc=decision_ts,
        resolved_at_utc=decision_ts + timedelta(days=1),
        resolved_outcome="YES" if label_yes == 1 else "NO",
        label_yes=label_yes,
        market_yes_prob_at_decision=yes_price,
        liquidity_usd=20_000.0,
        volume_24h_usd=8_000.0,
        structure_momentum=0.0,
        structure_score=0.5,
        scan_score=0.5,
        research_weighted_sentiment=0.0,
        research_evidence_strength=0.0,
        research_disagreement_score=0.0,
        research_findings_count=0,
        data_quality_flags=(),
    )


def _prediction(row: LabelRecord, *, fair_yes_prob: float, confidence: float) -> BaselinePrediction:
    side = "YES" if fair_yes_prob >= 0.5 else "NO"
    selected_market_price = row.market_yes_prob_at_decision if side == "YES" else (1.0 - row.market_yes_prob_at_decision)
    selected_fair_price = fair_yes_prob if side == "YES" else (1.0 - fair_yes_prob)
    edge = selected_fair_price - selected_market_price
    return BaselinePrediction(
        baseline_name="unit_test",
        row_id=row.row_id,
        predicted_yes_prob=fair_yes_prob,
        selected_side=side,
        selected_market_price=selected_market_price,
        selected_fair_price=selected_fair_price,
        edge=edge,
        confidence=confidence,
        market_yes_prob_at_decision=row.market_yes_prob_at_decision,
        label_yes=row.label_yes,
    )


def _service(tmp_path: Path) -> WalkForwardStrategyService:
    return WalkForwardStrategyService(
        historical_base_dir=tmp_path,
        default_dataset_id="ds",
        prediction_settings=PredictionSettings(),
    )


def test_simulate_fold_orders_decisions_chronologically(tmp_path: Path) -> None:
    service = _service(tmp_path)
    train_rows = (_label(1, decision_days=0, label_yes=1),)
    eval_rows = (
        _label(3, decision_days=3, label_yes=1),
        _label(2, decision_days=2, label_yes=0),
    )

    summary = service.simulate_fold(
        fold_index=1,
        split_name="test",
        train_rows=train_rows,
        eval_rows=eval_rows,
        predict_row=lambda row: _prediction(row, fair_yes_prob=0.7, confidence=0.9),
        thresholds=SimulationThresholds(min_confidence=0.1, min_edge_probability=0.01),
        assumptions=SimulationAssumptions(
            initial_bankroll_usd=1000.0,
            base_position_pct=0.05,
            max_position_pct=0.05,
            min_stake_usd=10.0,
            fee_bps=0,
            slippage_bps=0,
        ),
    )

    assert len(summary.decisions) == 2
    assert summary.decisions[0].decision_timestamp_utc <= summary.decisions[1].decision_timestamp_utc
    assert summary.decisions[0].row_id.startswith("m-2@")


def test_simulate_fold_rejects_temporal_leakage(tmp_path: Path) -> None:
    service = _service(tmp_path)
    train_rows = (_label(1, decision_days=3, label_yes=1),)
    eval_rows = (_label(2, decision_days=2, label_yes=1),)

    with pytest.raises(ValueError, match="not strictly after train end"):
        service.simulate_fold(
            fold_index=1,
            split_name="test",
            train_rows=train_rows,
            eval_rows=eval_rows,
            predict_row=lambda row: _prediction(row, fair_yes_prob=0.7, confidence=0.9),
            thresholds=SimulationThresholds(min_confidence=0.1, min_edge_probability=0.01),
            assumptions=SimulationAssumptions(
                initial_bankroll_usd=1000.0,
                base_position_pct=0.05,
                max_position_pct=0.05,
                min_stake_usd=10.0,
                fee_bps=0,
                slippage_bps=0,
            ),
        )


def test_simulate_fold_accounts_bankroll_and_pnl(tmp_path: Path) -> None:
    service = _service(tmp_path)
    train_rows = (_label(1, decision_days=0, label_yes=1),)
    eval_rows = (_label(2, decision_days=2, label_yes=1, yes_price=0.5),)

    summary = service.simulate_fold(
        fold_index=1,
        split_name="test",
        train_rows=train_rows,
        eval_rows=eval_rows,
        predict_row=lambda row: _prediction(row, fair_yes_prob=0.7, confidence=1.0),
        thresholds=SimulationThresholds(min_confidence=0.0, min_edge_probability=0.0),
        assumptions=SimulationAssumptions(
            initial_bankroll_usd=1000.0,
            base_position_pct=0.10,
            max_position_pct=0.10,
            min_stake_usd=10.0,
            fee_bps=0,
            slippage_bps=0,
        ),
    )

    assert summary.metrics.approved_predictions == 1
    assert summary.metrics.pnl_usd == pytest.approx(100.0, rel=1e-6)
    assert summary.metrics.roi == pytest.approx(0.10, rel=1e-6)
    assert summary.metrics.final_bankroll_usd == pytest.approx(1100.0, rel=1e-6)
