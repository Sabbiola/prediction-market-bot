"""Train a logistic regression model for Polymarket BTC Up/Down 5-minute markets.

Features are computed from COMPLETED 5-minute Binance candles preceding each decision point.
Labels: 1 if next candle close > next candle open (BTC goes UP in the 5-minute window).

Usage:
    # 1. Fetch data first (if not done):
    python scripts/btc_fetch_ohlcv.py --months 24

    # 2. Train the model:
    python scripts/btc_train_model.py

Output: data/models/v1/btc_updown_5m_v1.json
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path


# ---------------------------------------------------------------------------
# Feature names — must match btc_feature_enricher.py
# ---------------------------------------------------------------------------
FEATURES = [
    "f_btc_prev_return_1c",    # previous candle return: (close-open)/open
    "f_btc_prev_return_3c",    # 3-candle (15m) return
    "f_btc_prev_return_12c",   # 12-candle (1h) cumulative return
    "f_btc_prev_return_48c",   # 48-candle (4h) cumulative return
    "f_btc_rsi_14",            # RSI(14) on previous closes, normalised to [-1, 1]
    "f_btc_volume_ratio",      # current-candle volume / 20-period MA volume (log scale)
    "f_btc_volatility_12c",    # realized vol: std(returns[-12:])
    "f_btc_bb_position",       # Bollinger Band position (0=lower, 0.5=mid, 1=upper)
    "f_decision_hour_utc_sin", # cyclic hour encoding (sin)
    "f_decision_hour_utc_cos", # cyclic hour encoding (cos)
]

N_FEATURES = len(FEATURES)
REQUIRED_LOOKBACK = 50   # min candles needed before we can compute all features


# ---------------------------------------------------------------------------
# Feature computation helpers
# ---------------------------------------------------------------------------

def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.5  # neutral
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-(period + 1) + i] - closes[-(period + 1) + i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss < 1e-10:
        return 1.0
    rs = avg_gain / avg_loss
    return rs / (1.0 + rs)   # returns in [0, 1]; 0.5 = neutral


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def compute_features(candles: list[dict], idx: int) -> dict[str, float] | None:
    """Compute features for decision at `idx` using candles[:idx] (i.e. completed candles).

    Returns None if there are not enough preceding candles.
    """
    if idx < REQUIRED_LOOKBACK:
        return None

    prev = candles[idx - 1]   # last completed candle (the one just before the window)
    lookback = candles[max(0, idx - 48): idx]  # up to 48 prior candles

    closes = [c["close"] for c in lookback]
    opens  = [c["open"]  for c in lookback]
    vols   = [c["volume"] for c in lookback]

    # Returns
    prev_return_1c  = (prev["close"] - prev["open"]) / max(prev["open"], 1e-8)
    base_close_3c   = candles[max(0, idx - 3)]["close"]
    prev_return_3c  = (prev["close"] - base_close_3c) / max(base_close_3c, 1e-8)
    base_close_12c  = candles[max(0, idx - 12)]["close"]
    prev_return_12c = (prev["close"] - base_close_12c) / max(base_close_12c, 1e-8)
    base_close_48c  = candles[max(0, idx - 48)]["close"]
    prev_return_48c = (prev["close"] - base_close_48c) / max(base_close_48c, 1e-8)

    # RSI normalised to [-1, 1]: (rsi - 0.5) * 2
    rsi_raw = _rsi(closes, period=14)
    f_rsi = _clamp((rsi_raw - 0.5) * 2.0, -1.0, 1.0)

    # Volume ratio (log scale, clipped)
    recent_vol = prev["volume"]
    avg_vol_20 = sum(vols[-20:]) / max(len(vols[-20:]), 1)
    f_vol_ratio = _clamp(math.log(max(recent_vol, 1e-8) / max(avg_vol_20, 1e-8)), -3.0, 3.0)

    # Realized volatility: std of last-12 returns
    if len(closes) >= 13:
        last12_returns = [(closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-8) for i in range(-12, 0)]
        mean_r = sum(last12_returns) / 12
        var_r  = sum((r - mean_r) ** 2 for r in last12_returns) / 12
        f_vol_12c = _clamp(math.sqrt(var_r) * 100, 0.0, 5.0)  # in percentage points
    else:
        f_vol_12c = 0.0

    # Bollinger Band position: (close - lower) / (upper - lower)
    bb_closes = closes[-20:] if len(closes) >= 20 else closes
    bb_mean = sum(bb_closes) / len(bb_closes)
    bb_std  = math.sqrt(sum((c - bb_mean) ** 2 for c in bb_closes) / len(bb_closes))
    upper = bb_mean + 2 * bb_std
    lower = bb_mean - 2 * bb_std
    band_width = max(upper - lower, 1e-8)
    f_bb_pos = _clamp((prev["close"] - lower) / band_width, 0.0, 1.0) - 0.5  # centred at 0

    # Hour of day (cyclic)
    open_time_sec = candles[idx]["open_time_ms"] / 1000
    hour_frac = (open_time_sec % 86400) / 3600
    f_hour_sin = math.sin(2 * math.pi * hour_frac / 24)
    f_hour_cos = math.cos(2 * math.pi * hour_frac / 24)

    return {
        "f_btc_prev_return_1c":    _clamp(prev_return_1c,  -0.10, 0.10),
        "f_btc_prev_return_3c":    _clamp(prev_return_3c,  -0.15, 0.15),
        "f_btc_prev_return_12c":   _clamp(prev_return_12c, -0.20, 0.20),
        "f_btc_prev_return_48c":   _clamp(prev_return_48c, -0.30, 0.30),
        "f_btc_rsi_14":            f_rsi,
        "f_btc_volume_ratio":      f_vol_ratio,
        "f_btc_volatility_12c":    f_vol_12c,
        "f_btc_bb_position":       f_bb_pos,
        "f_decision_hour_utc_sin": f_hour_sin,
        "f_decision_hour_utc_cos": f_hour_cos,
    }


# ---------------------------------------------------------------------------
# Pure-Python logistic regression (gradient descent)
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    if x >= 0:
        e = math.exp(-x)
        return 1.0 / (1.0 + e)
    e = math.exp(x)
    return e / (1.0 + e)


def train_lr(
    X: list[list[float]],
    y: list[int],
    *,
    lr: float = 0.05,
    epochs: int = 80,
    l2: float = 1e-4,
    batch_size: int = 256,
    seed: int = 42,
) -> tuple[list[float], float]:
    """Returns (weights, bias)."""
    n, d = len(X), len(X[0])
    rng = random.Random(seed)
    weights = [rng.gauss(0, 0.01) for _ in range(d)]
    bias = 0.0

    indices = list(range(n))
    for epoch in range(epochs):
        rng.shuffle(indices)
        total_loss = 0.0
        for start in range(0, n, batch_size):
            batch = indices[start: start + batch_size]
            dw = [0.0] * d
            db = 0.0
            for i in batch:
                xi, yi = X[i], y[i]
                logit = sum(weights[j] * xi[j] for j in range(d)) + bias
                pred = _sigmoid(logit)
                err = pred - yi
                for j in range(d):
                    dw[j] += err * xi[j]
                db += err
                eps = 1e-10
                total_loss -= yi * math.log(pred + eps) + (1 - yi) * math.log(1 - pred + eps)
            bs = len(batch)
            for j in range(d):
                weights[j] -= lr * (dw[j] / bs + l2 * weights[j])
            bias -= lr * (db / bs)

        if (epoch + 1) % 20 == 0:
            acc = sum(1 for i in range(n) if (_sigmoid(sum(weights[j]*X[i][j] for j in range(d)) + bias) >= 0.5) == y[i]) / n
            print(f"  epoch {epoch+1}/{epochs}  loss={total_loss/n:.4f}  acc={acc:.4f}")

    return weights, bias


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    data_path = Path("data/btc/ohlcv_5m.jsonl")
    if not data_path.exists():
        print(f"ERROR: {data_path} not found. Run scripts/btc_fetch_ohlcv.py first.")
        return

    print(f"Loading candles from {data_path} ...")
    candles: list[dict] = []
    with data_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                candles.append(json.loads(line))
    candles.sort(key=lambda c: c["open_time_ms"])
    print(f"Loaded {len(candles)} candles ({candles[0]['open_time_ms']//1000} to {candles[-1]['open_time_ms']//1000})")

    # Build dataset
    print("Computing features ...")
    X_raw: list[list[float]] = []
    y_all: list[int] = []
    skipped = 0

    for idx in range(REQUIRED_LOOKBACK, len(candles) - 1):
        # Label: does the CURRENT window go UP?
        c = candles[idx]
        label = 1 if c["close"] > c["open"] else 0

        feats = compute_features(candles, idx)
        if feats is None:
            skipped += 1
            continue

        row = [feats[f] for f in FEATURES]
        X_raw.append(row)
        y_all.append(label)

    print(f"Dataset: {len(X_raw)} samples ({skipped} skipped), YES rate: {sum(y_all)/len(y_all):.3f}")

    # Standardise features
    n, d = len(X_raw), N_FEATURES
    means = [sum(X_raw[i][j] for i in range(n)) / n for j in range(d)]
    stds  = [
        max(math.sqrt(sum((X_raw[i][j] - means[j]) ** 2 for i in range(n)) / n), 1e-8)
        for j in range(d)
    ]
    X = [[(X_raw[i][j] - means[j]) / stds[j] for j in range(d)] for i in range(n)]

    # Train / validation split (last 20% as val, time-ordered)
    split = int(n * 0.80)
    X_train, y_train = X[:split], y_all[:split]
    X_val,   y_val   = X[split:], y_all[split:]
    print(f"Train: {len(X_train)}, Val: {len(X_val)}")

    print("Training logistic regression ...")
    weights, bias = train_lr(X_train, y_train, lr=0.05, epochs=100, l2=1e-4, batch_size=512)

    def accuracy(Xset, yset):
        correct = 0
        for xi, yi in zip(Xset, yset):
            logit = sum(weights[j] * xi[j] for j in range(d)) + bias
            pred = _sigmoid(logit)
            correct += (pred >= 0.5) == yi
        return correct / len(yset)

    acc_train = accuracy(X_train, y_train)
    acc_val   = accuracy(X_val,   y_val)
    print(f"\nTrain accuracy: {acc_train:.4f}  Val accuracy: {acc_val:.4f}")

    # Feature importances
    print("\nFeature weights (top by |weight|):")
    ranked = sorted(enumerate(weights), key=lambda x: abs(x[1]), reverse=True)
    for j, w in ranked[:10]:
        print(f"  {FEATURES[j]:<35}  w={w:+.4f}")

    # Save artifact
    out_path = Path("data/models/v1/btc_updown_5m_v1.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "artifact_type": "prediction_model_v2",
        "model_name": "btc_updown_5m_lr_v1",
        "model_version": "v1.0.0",
        "feature_schema_version": "btc-v1",
        "description": "Logistic regression for Polymarket BTC Up/Down 5-minute markets. "
                       "Features: Binance 5m OHLCV technical indicators.",
        "training_samples": n,
        "train_accuracy": round(acc_train, 6),
        "val_accuracy": round(acc_val, 6),
        "yes_rate": round(sum(y_all) / len(y_all), 6),
        "feature_columns": FEATURES,
        "required_features": ["f_btc_prev_return_1c", "f_btc_rsi_14"],
        "algorithm_payload": {
            "algorithm": "logistic_regression",
            "feature_names": FEATURES,
            "means": [round(m, 8) for m in means],
            "stds":  [round(s, 8) for s in stds],
            "weights": [round(w, 8) for w in weights],
            "bias": round(bias, 8),
        },
        "calibration": {
            "method": "none",
            "calibration_version": "none",
            "parameters": {},
        },
    }
    out_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"\nArtifact saved: {out_path}")
    print(f"  train_acc={acc_train:.4f}  val_acc={acc_val:.4f}  n={n}")


if __name__ == "__main__":
    main()
