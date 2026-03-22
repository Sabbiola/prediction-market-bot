from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def clamp_probability(value: float) -> float:
    return min(max(value, 1e-6), 1.0 - 1e-6)


def logit(value: float) -> float:
    p = clamp_probability(value)
    return math.log(p / (1.0 - p))


def sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


@dataclass(slots=True, frozen=True)
class PlattCalibrator:
    a: float
    b: float

    def predict(self, probability: float) -> float:
        x = logit(probability)
        return clamp_probability(sigmoid((self.a * x) + self.b))

    def to_dict(self) -> dict[str, float]:
        return {"a": self.a, "b": self.b}


def fit_platt_calibrator(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    learning_rate: float = 0.05,
    epochs: int = 800,
) -> PlattCalibrator:
    if not probabilities:
        return PlattCalibrator(a=1.0, b=0.0)
    a = 1.0
    b = 0.0
    sample_count = max(len(probabilities), 1)
    for _ in range(max(epochs, 1)):
        grad_a = 0.0
        grad_b = 0.0
        for probability, label in zip(probabilities, labels, strict=False):
            x = logit(probability)
            pred = sigmoid((a * x) + b)
            error = pred - float(label)
            grad_a += error * x
            grad_b += error
        a -= learning_rate * (grad_a / sample_count)
        b -= learning_rate * (grad_b / sample_count)
    return PlattCalibrator(a=a, b=b)


@dataclass(slots=True, frozen=True)
class IsotonicCalibrator:
    boundaries: tuple[float, ...]
    values: tuple[float, ...]

    def predict(self, probability: float) -> float:
        p = clamp_probability(probability)
        if not self.boundaries:
            return p
        for boundary, value in zip(self.boundaries, self.values, strict=False):
            if p <= boundary:
                return clamp_probability(value)
        return clamp_probability(self.values[-1])

    def to_dict(self) -> dict[str, list[float]]:
        return {"boundaries": list(self.boundaries), "values": list(self.values)}


def fit_isotonic_calibrator(probabilities: Sequence[float], labels: Sequence[int]) -> IsotonicCalibrator:
    if not probabilities:
        return IsotonicCalibrator(boundaries=(), values=())
    ordered = sorted(
        [(clamp_probability(float(probability)), int(label)) for probability, label in zip(probabilities, labels, strict=False)],
        key=lambda item: item[0],
    )
    starts = [row[0] for row in ordered]
    ends = [row[0] for row in ordered]
    sum_y = [float(row[1]) for row in ordered]
    weights = [1.0 for _ in ordered]

    index = 0
    while index < len(sum_y) - 1:
        mean_left = sum_y[index] / weights[index]
        mean_right = sum_y[index + 1] / weights[index + 1]
        if mean_left <= mean_right:
            index += 1
            continue
        sum_y[index] += sum_y[index + 1]
        weights[index] += weights[index + 1]
        ends[index] = ends[index + 1]
        del sum_y[index + 1]
        del weights[index + 1]
        del starts[index + 1]
        del ends[index + 1]
        if index > 0:
            index -= 1

    boundaries = tuple(float(boundary) for boundary in ends)
    values = tuple(float(sum_value / weight) for sum_value, weight in zip(sum_y, weights, strict=False))
    return IsotonicCalibrator(boundaries=boundaries, values=values)
