"""BTC price feature enricher for Polymarket BTC Up/Down markets.

Fetches recent BTC/USDT 5-minute candles from Binance and computes
technical features that feed into the BTC prediction model.

Feature names MUST match those in scripts/btc_train_model.py:
    f_btc_prev_return_1c, f_btc_prev_return_3c, f_btc_prev_return_12c,
    f_btc_prev_return_48c, f_btc_rsi_14, f_btc_volume_ratio,
    f_btc_volatility_12c, f_btc_bb_position,
    f_decision_hour_utc_sin, f_decision_hour_utc_cos
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from datetime import UTC, datetime
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_BTC_UPDOWN_PATTERNS = (
    "btc-up-or-down",
    "btc-updown",
    "bitcoin-up-or-down",
    "bitcoin-updown",
)

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
N_CANDLES = 60   # 60 x 5m = 5h lookback (enough for all features)
CACHE_TTL_SEC = 60  # re-fetch at most once per minute


def is_btc_updown_market(slug: str, question: str) -> bool:
    """Return True if this Polymarket market is a BTC Up/Down series market."""
    text = (slug + " " + question).lower()
    return any(p in text for p in _BTC_UPDOWN_PATTERNS)


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.5
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
    return rs / (1.0 + rs)


class BtcFeatureEnricher:
    """Fetches live BTC/USDT 5m candles and computes features for the BTC model.

    Thread-safe: uses an internal lock + TTL cache so the bot's parallel
    pipeline won't hammer Binance on every market candidate.
    """

    def __init__(
        self,
        *,
        symbol: str = SYMBOL,
        interval: str = INTERVAL,
        n_candles: int = N_CANDLES,
        cache_ttl_sec: float = CACHE_TTL_SEC,
        timeout_sec: float = 8.0,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.n_candles = n_candles
        self.cache_ttl_sec = cache_ttl_sec
        self.timeout_sec = timeout_sec
        self._lock = Lock()
        self._cache: list[dict[str, Any]] = []
        self._cache_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_features(self) -> dict[str, float]:
        """Return a dict of BTC technical features, or empty dict on error."""
        candles = self._get_candles()
        if not candles or len(candles) < 15:
            logger.warning("btc_feature_enricher_insufficient_candles n=%d", len(candles))
            return {}
        try:
            return self._compute_features(candles)
        except Exception as exc:
            logger.warning("btc_feature_enricher_compute_error error=%s", exc)
            return {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_candles(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            if self._cache and (now - self._cache_at) < self.cache_ttl_sec:
                return self._cache
            try:
                candles = self._fetch_candles()
                self._cache = candles
                self._cache_at = now
                return candles
            except Exception as exc:
                logger.warning("btc_feature_enricher_fetch_error error=%s", exc)
                return self._cache  # return stale cache on error

    def _fetch_candles(self) -> list[dict[str, Any]]:
        url = (
            f"{BINANCE_KLINES_URL}"
            f"?symbol={self.symbol}&interval={self.interval}&limit={self.n_candles}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "prediction-market-bot/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            raw = json.loads(resp.read())
        candles = []
        for c in raw:
            candles.append({
                "open_time_ms": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        return candles

    def _compute_features(self, candles: list[dict[str, Any]]) -> dict[str, float]:
        # Use all but the last candle (which may be incomplete)
        completed = candles[:-1]
        if len(completed) < 14:
            return {}

        prev = completed[-1]
        closes = [c["close"] for c in completed]
        vols   = [c["volume"] for c in completed]

        # Returns using previous completed candles
        prev_return_1c  = (prev["close"] - prev["open"]) / max(prev["open"], 1e-8)
        base_3c  = completed[max(0, len(completed) - 3)]["close"]
        prev_return_3c  = (prev["close"] - base_3c)  / max(base_3c,  1e-8)
        base_12c = completed[max(0, len(completed) - 12)]["close"]
        prev_return_12c = (prev["close"] - base_12c) / max(base_12c, 1e-8)
        base_48c = completed[max(0, len(completed) - 48)]["close"]
        prev_return_48c = (prev["close"] - base_48c) / max(base_48c, 1e-8)

        # RSI (normalised to [-1,1])
        rsi_raw = _rsi(closes, period=14)
        f_rsi = _clamp((rsi_raw - 0.5) * 2.0, -1.0, 1.0)

        # Volume ratio (log)
        avg_vol = sum(vols[-20:]) / max(len(vols[-20:]), 1)
        f_vol_ratio = _clamp(math.log(max(prev["volume"], 1e-8) / max(avg_vol, 1e-8)), -3.0, 3.0)

        # Realized volatility (12 candles)
        if len(closes) >= 13:
            rets = [(closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-8) for i in range(-12, 0)]
            mean_r = sum(rets) / 12
            f_vol_12c = _clamp(math.sqrt(sum((r - mean_r) ** 2 for r in rets) / 12) * 100, 0.0, 5.0)
        else:
            f_vol_12c = 0.0

        # Bollinger Band position (centred at 0)
        bb_closes = closes[-20:]
        bb_mean = sum(bb_closes) / len(bb_closes)
        bb_std  = math.sqrt(sum((c - bb_mean) ** 2 for c in bb_closes) / len(bb_closes))
        upper = bb_mean + 2 * bb_std
        lower = bb_mean - 2 * bb_std
        band_width = max(upper - lower, 1e-8)
        f_bb_pos = _clamp((prev["close"] - lower) / band_width, 0.0, 1.0) - 0.5

        # Hour of day (cyclic) — use current time
        now_utc = datetime.now(UTC)
        hour_frac = now_utc.hour + now_utc.minute / 60.0
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
