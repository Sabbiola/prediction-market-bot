from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from prediction_market_bot.strategy_research.training import algorithms
from prediction_market_bot.strategy_research.training.models import TrainingFeatureRow


def _row(*, row_id: str, label_yes: int, feature_value: float) -> TrainingFeatureRow:
    return TrainingFeatureRow(
        row_id=row_id,
        market_id=f"m-{row_id}",
        event_id=f"e-{row_id}",
        category="test",
        decision_timestamp_utc=datetime(2026, 1, 1, tzinfo=UTC),
        label_yes=label_yes,
        features={"f_signal": feature_value},
    )


def test_train_xgboost_candidate_requires_two_label_classes() -> None:
    rows = (
        _row(row_id="1", label_yes=1, feature_value=0.4),
        _row(row_id="2", label_yes=1, feature_value=0.5),
    )

    model, reason = algorithms.train_xgboost_candidate(rows, ("f_signal",))

    assert model is None
    assert reason == "xgboost candidate requires both label classes in train split"


def test_train_xgboost_candidate_returns_explicit_training_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeNumpy:
        @staticmethod
        def array(value: Any, dtype: Any = None) -> Any:
            del dtype
            return value

    class _FakeClassifier:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def fit(self, matrix: Any, labels: Any) -> None:
            del matrix, labels
            raise RuntimeError("boom")

    class _FakeXGBoost:
        XGBClassifier = _FakeClassifier

    def _fake_import_module(name: str) -> Any:
        if name == "xgboost":
            return _FakeXGBoost
        if name == "numpy":
            return _FakeNumpy
        raise ImportError(name)

    monkeypatch.setattr(algorithms.importlib, "import_module", _fake_import_module)
    rows = (
        _row(row_id="1", label_yes=1, feature_value=0.4),
        _row(row_id="2", label_yes=0, feature_value=0.5),
    )

    model, reason = algorithms.train_xgboost_candidate(rows, ("f_signal",))

    assert model is None
    assert reason == "xgboost candidate training failed (RuntimeError): boom"
