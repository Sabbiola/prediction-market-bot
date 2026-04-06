#!/usr/bin/env python3
"""Bootstrap synthetic prediction model for PAPER_LIVE autonomous mode.

Generates 5000 synthetic market scenarios, trains a logistic regression
that combines market price (crowd estimate) with research signals, and
writes the model artifact JSON ready for runtime inference.

Run from the project root:
    python scripts/bootstrap_model.py

The model learns:
  - Market price is a strong prior (crowd is usually right)
  - Research sentiment adjusts probability when evidence is strong
  - Illiquid / high-spread markets can deviate more from market consensus
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

# ── Feature columns ────────────────────────────────────────────────────────
# Must match exactly the keys produced by prediction_runtime_features.py
FEATURES = [
    "f_market_yes_price",
    "f_market_price_logit",
    "f_market_price_distance_0_5",
    "f_spread_bps",
    "f_liquidity_usd",
    "f_volume_24h_usd",
    "f_hours_to_close",
    "f_price_move_1h",
    "f_realized_volatility_24h",
    "f_research_findings_count",
    "f_research_weighted_sentiment",
    "f_research_evidence_strength",
    "f_research_avg_credibility",
    "f_research_effective_credibility",
    "f_research_disagreement",
    "f_research_contradiction_rate",
]


# ── Math helpers ────────────────────────────────────────────────────────────

def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _clamp(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _beta(rng: random.Random, a: float, b: float) -> float:
    """Approximate beta variate via ratio of gammas."""
    g1 = rng.gammavariate(a, 1.0)
    g2 = rng.gammavariate(b, 1.0)
    return _clamp(g1 / (g1 + g2), 0.0, 1.0)


# ── Synthetic data generation ───────────────────────────────────────────────

def _generate_row(rng: random.Random) -> tuple[list[float], float]:
    """Produce one synthetic (feature_vector, label) pair."""
    # Market features — match distributions of live Polymarket markets
    yes_price = rng.uniform(0.05, 0.95)
    price_logit = math.log(yes_price / (1.0 - yes_price))
    distance_0_5 = abs(yes_price - 0.5)
    # Spread: most markets 50-300 bps, some illiquid up to 1000
    spread_bps = _clamp(rng.expovariate(1.0 / 120.0) + 20.0, 20.0, 1000.0)
    # Liquidity: log-normal; most markets $10k-$500k
    liquidity_usd = max(500.0, math.exp(rng.gauss(10.2, 1.6)))
    volume_24h = max(0.0, math.exp(rng.gauss(8.8, 1.8)))
    hours_to_close = rng.uniform(6.0, 240.0)
    price_move = rng.gauss(0.0, 0.018)
    volatility = abs(price_move)

    # Research features — not every market has strong research
    has_research = rng.random() < 0.65
    if has_research:
        findings_count = float(max(1, int(rng.gauss(4.5, 2.5))))
        # Sentiment: slightly correlated with true outcome noise
        raw_sentiment = rng.gauss(0.0, 0.45)
        sentiment = _clamp(raw_sentiment, -1.0, 1.0)
        evidence_strength = _beta(rng, 2.0, 2.5)   # avg ~0.44
        avg_credibility = _beta(rng, 3.5, 1.5)     # avg ~0.70
        disagreement = _beta(rng, 1.2, 5.0)        # usually low
    else:
        findings_count = 0.0
        sentiment = 0.0
        evidence_strength = 0.0
        avg_credibility = 0.0
        disagreement = 0.0

    effective_credibility = avg_credibility * (1.0 - disagreement)
    contradiction_rate = _clamp(disagreement * 0.8 + rng.uniform(0.0, 0.08), 0.0, 1.0)

    # ── True probability ────────────────────────────────────────────────────
    # Market price is the base prior from the crowd.
    # Research adjusts when evidence is strong and consistent.
    research_reliability = evidence_strength * (1.0 - disagreement) * avg_credibility
    research_adj = sentiment * research_reliability * 0.42

    # Illiquid markets (high spread) deviate more from crowd consensus
    spread_factor = 1.0 + _clamp((spread_bps - 100.0) / 1000.0, 0.0, 0.5)
    true_prob = _clamp(yes_price + research_adj * spread_factor, 0.04, 0.96)

    # Outcome noise — prediction markets are inherently uncertain
    noise_scale = 0.11 + (1.0 - evidence_strength) * 0.09
    label_prob = _clamp(true_prob + rng.gauss(0.0, noise_scale), 0.01, 0.99)
    label = 1.0 if rng.random() < label_prob else 0.0

    features = [
        yes_price,
        price_logit,
        distance_0_5,
        spread_bps,
        liquidity_usd,
        volume_24h,
        hours_to_close,
        price_move,
        volatility,
        findings_count,
        sentiment,
        evidence_strength,
        avg_credibility,
        effective_credibility,
        disagreement,
        contradiction_rate,
    ]
    return features, label


# ── Training ────────────────────────────────────────────────────────────────

def _compute_stats(
    X: list[list[float]], n_features: int
) -> tuple[list[float], list[float]]:
    n = len(X)
    means: list[float] = []
    stds: list[float] = []
    for j in range(n_features):
        vals = [X[i][j] for i in range(n)]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = math.sqrt(max(var, 1e-12))
        means.append(mean)
        stds.append(std if std > 1e-8 else 1.0)
    return means, stds


def _train(
    X: list[list[float]],
    y: list[float],
    means: list[float],
    stds: list[float],
    n_features: int,
    *,
    epochs: int = 1000,
    lr: float = 0.06,
    l2: float = 3e-4,
) -> tuple[list[float], float]:
    n = len(X)
    weights = [0.0] * n_features
    bias = 0.0
    for epoch in range(epochs):
        gw = [0.0] * n_features
        gb = 0.0
        for i in range(n):
            z = bias
            for j in range(n_features):
                z += weights[j] * (X[i][j] - means[j]) / stds[j]
            pred = _sigmoid(z)
            err = pred - y[i]
            gb += err
            for j in range(n_features):
                gw[j] += err * (X[i][j] - means[j]) / stds[j] + l2 * weights[j]
        bias -= lr * gb / n
        for j in range(n_features):
            weights[j] -= lr * gw[j] / n
        # Adaptive learning rate decay
        if (epoch + 1) % 200 == 0:
            lr *= 0.85
    return weights, bias


def _evaluate(
    X: list[list[float]],
    y: list[float],
    means: list[float],
    stds: list[float],
    weights: list[float],
    bias: float,
    n_features: int,
) -> dict[str, float]:
    n = len(X)
    correct = 0
    log_loss = 0.0
    for i in range(n):
        z = bias
        for j in range(n_features):
            z += weights[j] * (X[i][j] - means[j]) / stds[j]
        pred = _sigmoid(z)
        pred_c = _clamp(pred, 1e-7, 1.0 - 1e-7)
        if (pred >= 0.5) == (y[i] >= 0.5):
            correct += 1
        log_loss += -(y[i] * math.log(pred_c) + (1.0 - y[i]) * math.log(1.0 - pred_c))
    return {"accuracy": correct / n, "log_loss": log_loss / n}


# ── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    out_dir = Path("data/models/v1")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "logistic_regression_v1.json"

    n_samples = 5000
    n_features = len(FEATURES)
    print(f"Generating {n_samples} synthetic market scenarios...")
    rng = random.Random(2024_10_01)
    X: list[list[float]] = []
    y: list[float] = []
    for _ in range(n_samples):
        row, label = _generate_row(rng)
        X.append(row)
        y.append(label)

    pos_rate = sum(y) / n_samples
    print(f"  Label distribution: {pos_rate:.1%} YES, {1 - pos_rate:.1%} NO")

    # Split: 80% train, 20% validation
    split = int(n_samples * 0.80)
    X_train, y_train = X[:split], y[:split]
    X_val, y_val = X[split:], y[split:]

    print(f"\nTraining logistic regression ({len(X_train)} samples, {n_features} features)...")
    means, stds = _compute_stats(X_train, n_features)
    weights, bias = _train(X_train, y_train, means, stds, n_features, epochs=1000, lr=0.06, l2=3e-4)

    train_metrics = _evaluate(X_train, y_train, means, stds, weights, bias, n_features)
    val_metrics = _evaluate(X_val, y_val, means, stds, weights, bias, n_features)
    print(f"  Train  accuracy={train_metrics['accuracy']:.3f}  log_loss={train_metrics['log_loss']:.4f}")
    print(f"  Val    accuracy={val_metrics['accuracy']:.3f}  log_loss={val_metrics['log_loss']:.4f}")

    # ── Feature importance ──────────────────────────────────────────────────
    print("\nTop feature weights (|w|):")
    importance = sorted(
        zip(FEATURES, weights, strict=False),
        key=lambda x: abs(x[1]),
        reverse=True,
    )
    for name, w in importance[:10]:
        print(f"  {w:+.4f}  {name}")

    # ── Write artifact ──────────────────────────────────────────────────────
    artifact: dict = {
        "artifact_type": "prediction_model_v2",
        "model_name": "logistic_regression_v1",
        "model_version": "v1.0.0",
        "feature_schema_version": "v1",
        "feature_columns": list(FEATURES),
        "required_features": ["f_market_yes_price"],
        "training_meta": {
            "n_train": len(X_train),
            "n_val": len(X_val),
            "train_accuracy": round(train_metrics["accuracy"], 4),
            "val_accuracy": round(val_metrics["accuracy"], 4),
            "train_log_loss": round(train_metrics["log_loss"], 5),
            "val_log_loss": round(val_metrics["log_loss"], 5),
            "description": (
                "Bootstrap LR trained on synthetic Polymarket-like data. "
                "Market price is the strong prior; research signals adjust "
                "probability when evidence is high and consistent."
            ),
        },
        "algorithm_payload": {
            "algorithm": "logistic_regression",
            "feature_names": list(FEATURES),
            "means": [round(m, 8) for m in means],
            "stds": [round(s, 8) for s in stds],
            "weights": [round(w, 8) for w in weights],
            "bias": round(bias, 8),
            "epochs": 1000,
            "learning_rate": 0.06,
            "l2_penalty": 3e-4,
        },
        "calibration": {
            "artifact_type": "prediction_calibration_v2",
            "method": "none",
            "calibration_version": "v1.0.0-bootstrap",
        },
    }

    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"\nArtifact saved: {out_path}")


if __name__ == "__main__":
    main()
