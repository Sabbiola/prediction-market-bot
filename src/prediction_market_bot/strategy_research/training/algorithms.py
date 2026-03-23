from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import Any, Sequence

from .calibration import clamp_probability, sigmoid
from .models import TrainingFeatureRow


class ModelAlgorithm:
    def predict_proba(self, row: TrainingFeatureRow) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def feature_importance(self, top_k: int = 20) -> tuple[tuple[str, float], ...]:  # pragma: no cover - interface
        raise NotImplementedError

    def metadata(self) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def serializable_payload(self) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(slots=True)
class LogisticRegressionModel(ModelAlgorithm):
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float
    epochs: int
    learning_rate: float
    l2_penalty: float

    def predict_proba(self, row: TrainingFeatureRow) -> float:
        total = self.bias
        for index, name in enumerate(self.feature_names):
            value = float(row.features.get(name, 0.0))
            normalized = (value - self.means[index]) / self.stds[index]
            total += self.weights[index] * normalized
        return clamp_probability(sigmoid(total))

    def feature_importance(self, top_k: int = 20) -> tuple[tuple[str, float], ...]:
        scored = sorted(
            [
                (name, abs(weight))
                for name, weight in zip(self.feature_names, self.weights, strict=False)
            ],
            key=lambda row: row[1],
            reverse=True,
        )
        return tuple((name, round(score, 8)) for name, score in scored[:max(top_k, 1)])

    def metadata(self) -> dict[str, Any]:
        return {
            "algorithm": "logistic_regression",
            "epochs": self.epochs,
            "learning_rate": self.learning_rate,
            "l2_penalty": self.l2_penalty,
            "feature_count": len(self.feature_names),
        }

    def serializable_payload(self) -> dict[str, Any]:
        return {
            "algorithm": "logistic_regression",
            "feature_names": list(self.feature_names),
            "means": list(self.means),
            "stds": list(self.stds),
            "weights": list(self.weights),
            "bias": self.bias,
            "epochs": self.epochs,
            "learning_rate": self.learning_rate,
            "l2_penalty": self.l2_penalty,
        }


@dataclass(slots=True)
class DecisionStumpModel(ModelAlgorithm):
    feature_name: str
    threshold: float
    left_probability: float
    right_probability: float
    global_probability: float
    candidate_thresholds: int

    def predict_proba(self, row: TrainingFeatureRow) -> float:
        if not self.feature_name:
            return clamp_probability(self.global_probability)
        value = float(row.features.get(self.feature_name, 0.0))
        if value <= self.threshold:
            return clamp_probability(self.left_probability)
        return clamp_probability(self.right_probability)

    def feature_importance(self, top_k: int = 20) -> tuple[tuple[str, float], ...]:
        if not self.feature_name:
            return ()
        return ((self.feature_name, 1.0),)

    def metadata(self) -> dict[str, Any]:
        return {
            "algorithm": "tree_baseline",
            "feature_name": self.feature_name,
            "threshold": self.threshold,
            "candidate_thresholds": self.candidate_thresholds,
        }

    def serializable_payload(self) -> dict[str, Any]:
        return {
            "algorithm": "tree_baseline",
            "feature_name": self.feature_name,
            "threshold": self.threshold,
            "left_probability": self.left_probability,
            "right_probability": self.right_probability,
            "global_probability": self.global_probability,
            "candidate_thresholds": self.candidate_thresholds,
        }


@dataclass(slots=True)
class XGBoostModel(ModelAlgorithm):
    feature_names: tuple[str, ...]
    model: Any
    hyperparameters: dict[str, Any]

    def predict_proba(self, row: TrainingFeatureRow) -> float:
        numpy = importlib.import_module("numpy")
        vector = [float(row.features.get(name, 0.0)) for name in self.feature_names]
        matrix = numpy.array([vector], dtype=float)
        probs = self.model.predict_proba(matrix)
        return clamp_probability(float(probs[0][1]))

    def feature_importance(self, top_k: int = 20) -> tuple[tuple[str, float], ...]:
        importances = getattr(self.model, "feature_importances_", None)
        if importances is None:
            return ()
        scored: list[tuple[str, float]] = []
        for index, score in enumerate(importances):
            if index >= len(self.feature_names):
                continue
            scored.append((self.feature_names[index], float(score)))
        scored.sort(key=lambda row: row[1], reverse=True)
        return tuple((name, round(score, 8)) for name, score in scored[:max(top_k, 1)])

    def metadata(self) -> dict[str, Any]:
        return {
            "algorithm": "xgboost_candidate",
            "feature_count": len(self.feature_names),
            "hyperparameters": dict(self.hyperparameters),
        }

    def serializable_payload(self) -> dict[str, Any]:
        return {
            "algorithm": "xgboost_candidate",
            "feature_names": list(self.feature_names),
            "hyperparameters": dict(self.hyperparameters),
        }


def train_logistic_regression(
    rows: Sequence[TrainingFeatureRow],
    feature_names: Sequence[str],
    *,
    epochs: int = 500,
    learning_rate: float = 0.05,
    l2_penalty: float = 1e-4,
) -> LogisticRegressionModel:
    features = tuple(feature_names)
    n_features = len(features)
    if n_features == 0:
        return LogisticRegressionModel(
            feature_names=(),
            means=(),
            stds=(),
            weights=(),
            bias=0.0,
            epochs=epochs,
            learning_rate=learning_rate,
            l2_penalty=l2_penalty,
        )
    means: list[float] = []
    stds: list[float] = []
    for name in features:
        values = [float(row.features.get(name, 0.0)) for row in rows]
        mean = sum(values) / max(len(values), 1)
        variance = sum((value - mean) ** 2 for value in values) / max(len(values), 1)
        std = math.sqrt(max(variance, 1e-12))
        means.append(mean)
        stds.append(std if std > 1e-8 else 1.0)

    weights = [0.0 for _ in range(n_features)]
    bias = 0.0
    sample_count = max(len(rows), 1)
    for _ in range(max(epochs, 1)):
        grad_w = [0.0 for _ in range(n_features)]
        grad_b = 0.0
        for row in rows:
            total = bias
            normalized: list[float] = []
            for index, name in enumerate(features):
                value = float(row.features.get(name, 0.0))
                norm = (value - means[index]) / stds[index]
                normalized.append(norm)
                total += weights[index] * norm
            pred = sigmoid(total)
            error = pred - float(row.label_yes)
            grad_b += error
            for index in range(n_features):
                grad_w[index] += (error * normalized[index]) + (l2_penalty * weights[index])
        bias -= learning_rate * (grad_b / sample_count)
        for index in range(n_features):
            weights[index] -= learning_rate * (grad_w[index] / sample_count)

    return LogisticRegressionModel(
        feature_names=tuple(features),
        means=tuple(means),
        stds=tuple(stds),
        weights=tuple(weights),
        bias=bias,
        epochs=epochs,
        learning_rate=learning_rate,
        l2_penalty=l2_penalty,
    )


def train_tree_baseline(
    rows: Sequence[TrainingFeatureRow],
    feature_names: Sequence[str],
) -> DecisionStumpModel:
    if not rows:
        return DecisionStumpModel(
            feature_name="",
            threshold=0.0,
            left_probability=0.5,
            right_probability=0.5,
            global_probability=0.5,
            candidate_thresholds=0,
        )
    global_probability = sum(float(row.label_yes) for row in rows) / len(rows)
    best_loss = float("inf")
    best_feature = ""
    best_threshold = 0.0
    best_left_probability = clamp_probability(global_probability)
    best_right_probability = clamp_probability(global_probability)
    evaluated_thresholds = 0
    for feature_name in feature_names:
        values = sorted({float(row.features.get(feature_name, 0.0)) for row in rows})
        if len(values) <= 1:
            thresholds = values
        elif len(values) > 30:
            step = max(len(values) // 20, 1)
            thresholds = [values[index] for index in range(1, len(values), step)]
        else:
            thresholds = [(values[index] + values[index + 1]) / 2.0 for index in range(len(values) - 1)]
        for threshold in thresholds:
            left = [row for row in rows if float(row.features.get(feature_name, 0.0)) <= threshold]
            right = [row for row in rows if float(row.features.get(feature_name, 0.0)) > threshold]
            if not left or not right:
                continue
            left_probability = clamp_probability(sum(float(row.label_yes) for row in left) / len(left))
            right_probability = clamp_probability(sum(float(row.label_yes) for row in right) / len(right))
            loss = 0.0
            for row in left:
                label = float(row.label_yes)
                loss += -((label * math.log(left_probability)) + ((1.0 - label) * math.log(1.0 - left_probability)))
            for row in right:
                label = float(row.label_yes)
                loss += -((label * math.log(right_probability)) + ((1.0 - label) * math.log(1.0 - right_probability)))
            evaluated_thresholds += 1
            if loss < best_loss:
                best_loss = loss
                best_feature = feature_name
                best_threshold = float(threshold)
                best_left_probability = float(left_probability)
                best_right_probability = float(right_probability)
    return DecisionStumpModel(
        feature_name=best_feature,
        threshold=best_threshold,
        left_probability=best_left_probability,
        right_probability=best_right_probability,
        global_probability=float(global_probability),
        candidate_thresholds=evaluated_thresholds,
    )


def train_xgboost_candidate(
    rows: Sequence[TrainingFeatureRow],
    feature_names: Sequence[str],
) -> tuple[XGBoostModel | None, str]:
    if not rows:
        return None, "no training rows available"
    features = tuple(feature_names)
    if not features:
        return None, "no feature columns available for xgboost candidate"
    unique_labels = {int(row.label_yes) for row in rows}
    if len(unique_labels) < 2:
        return None, "xgboost candidate requires both label classes in train split"
    try:
        xgboost = importlib.import_module("xgboost")
        numpy = importlib.import_module("numpy")
    except Exception:
        return None, "xgboost dependency not installed (install optional research extras)"
    try:
        matrix = numpy.array([[float(row.features.get(name, 0.0)) for name in features] for row in rows], dtype=float)
        labels = numpy.array([int(row.label_yes) for row in rows], dtype=int)
        hyperparameters = {
            "n_estimators": 80,
            "max_depth": 3,
            "learning_rate": 0.1,
            "subsample": 0.9,
            "colsample_bytree": 0.9,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
            "verbosity": 0,
        }
        model = xgboost.XGBClassifier(**hyperparameters)
        model.fit(matrix, labels)
    except Exception as exc:
        return None, f"xgboost candidate training failed ({exc.__class__.__name__}): {exc}"
    return (
        XGBoostModel(
            feature_names=features,
            model=model,
            hyperparameters=hyperparameters,
        ),
        "",
    )
