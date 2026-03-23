from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prediction_market_bot.strategy_research.benchmarking import (
    LabelRecord,
    build_holdout_fold,
    build_walk_forward_folds,
    fit_training_stats,
    temporal_leakage_errors,
)


def _label(idx: int, *, days: int, category: str, label_yes: int) -> LabelRecord:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=days)
    return LabelRecord(
        row_id=f"m-{idx}@{timestamp.isoformat()}",
        market_id=f"m-{idx}",
        event_id=f"e-{idx}",
        category=category,
        market_title=f"market-{idx}",
        decision_timestamp_utc=timestamp,
        resolved_at_utc=timestamp + timedelta(days=1),
        resolved_outcome="YES" if label_yes == 1 else "NO",
        label_yes=label_yes,
        market_yes_prob_at_decision=0.5,
        liquidity_usd=10000.0,
        volume_24h_usd=2000.0,
        structure_momentum=0.0,
        structure_score=0.5,
        scan_score=0.5,
        research_weighted_sentiment=0.0,
        research_evidence_strength=0.0,
        research_disagreement_score=0.0,
        research_findings_count=0,
        data_quality_flags=(),
    )


def test_holdout_split_has_no_temporal_leakage() -> None:
    rows = [
        _label(1, days=0, category="a", label_yes=1),
        _label(2, days=1, category="a", label_yes=0),
        _label(3, days=2, category="b", label_yes=1),
        _label(4, days=3, category="b", label_yes=0),
        _label(5, days=4, category="c", label_yes=1),
    ]
    fold = build_holdout_fold(rows, train_ratio=0.6, validation_ratio=0.2)
    assert temporal_leakage_errors(fold) == []


def test_walk_forward_split_has_no_temporal_leakage() -> None:
    rows = [_label(i + 1, days=i, category="cat", label_yes=(i % 2)) for i in range(40)]
    folds = build_walk_forward_folds(
        rows,
        train_days=15,
        validation_days=8,
        test_days=8,
        step_days=5,
        max_folds=3,
    )
    assert len(folds) >= 1
    for fold in folds:
        assert temporal_leakage_errors(fold) == []


def test_category_prior_uses_only_train_rows() -> None:
    train_rows = [
        _label(1, days=0, category="sports", label_yes=1),
        _label(2, days=1, category="sports", label_yes=1),
        _label(3, days=2, category="macro", label_yes=0),
    ]
    stats = fit_training_stats(train_rows)
    assert stats.category_prior_yes["sports"] == 1.0
    assert stats.category_prior_yes["macro"] == 0.0
    # Unseen category must fallback to global train prior only.
    assert stats.category_prior_yes.get("politics") is None
    assert 0.0 <= stats.global_prior_yes <= 1.0
