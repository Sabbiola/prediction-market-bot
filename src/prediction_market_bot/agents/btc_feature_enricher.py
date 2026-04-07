"""BTC price + derivatives feature enricher for Polymarket BTC Up/Down markets.

Fetches live data from Binance:
  1. BTC/USDT 5m candles (spot) -> OHLCV technicals
  2. Funding rate (futures) -> long/short crowd sentiment
  3. Global long/short account ratio (futures) -> position sentiment
  4. Taker buy/sell ratio (futures) -> aggressor flow

Feature names MUST match those produced by scripts/btc_train_exhaustive.py.
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
FAPI_BASE = "https://fapi.binance.com"
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


def _safe_json_get(url: str, *, timeout: float = 8.0) -> Any:
    """Fetch JSON from URL, return None on error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "prediction-market-bot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


class BtcFeatureEnricher:
    """Fetches live BTC/USDT data and computes features for the BTC model.

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
        self._cache: dict[str, Any] = {}
        self._cache_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_features(self) -> dict[str, float]:
        """Return a dict of BTC technical + derivatives features, or empty dict on error."""
        data = self._get_cached_data()
        candles = data.get("candles", [])
        if not candles or len(candles) < 15:
            logger.warning("btc_feature_enricher_insufficient_candles n=%d", len(candles))
            return {}
        try:
            feats = self._compute_ohlcv_features(candles)
            feats.update(self._compute_derivatives_features(data))
            return feats
        except Exception as exc:
            logger.warning("btc_feature_enricher_compute_error error=%s", exc)
            return {}

    # ------------------------------------------------------------------
    # Cache layer — fetches all data sources in one go
    # ------------------------------------------------------------------

    def _get_cached_data(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._cache and (now - self._cache_at) < self.cache_ttl_sec:
                return self._cache
            try:
                data = self._fetch_all()
                self._cache = data
                self._cache_at = now
                return data
            except Exception as exc:
                logger.warning("btc_feature_enricher_fetch_error error=%s", exc)
                return self._cache  # stale cache on error

    def _fetch_all(self) -> dict[str, Any]:
        """Fetch candles + derivatives in sequence (fast, ~200ms total)."""
        candles = self._fetch_candles()

        # Funding rate — latest entry
        funding_url = f"{FAPI_BASE}/fapi/v1/fundingRate?symbol={self.symbol}&limit=1"
        funding_raw = _safe_json_get(funding_url, timeout=self.timeout_sec)
        funding_rate = None
        if isinstance(funding_raw, list) and funding_raw:
            funding_rate = float(funding_raw[-1].get("fundingRate", 0))

        # Long/short account ratio — latest 5m
        ls_url = f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio?symbol={self.symbol}&period=5m&limit=1"
        ls_raw = _safe_json_get(ls_url, timeout=self.timeout_sec)
        ls_ratio = None
        if isinstance(ls_raw, list) and ls_raw:
            ls_ratio = float(ls_raw[-1].get("longShortRatio", 1.0))

        # Taker buy/sell ratio — latest 5m
        taker_url = f"{FAPI_BASE}/futures/data/takerlongshortRatio?symbol={self.symbol}&period=5m&limit=1"
        taker_raw = _safe_json_get(taker_url, timeout=self.timeout_sec)
        taker_ratio = None
        if isinstance(taker_raw, list) and taker_raw:
            taker_ratio = float(taker_raw[-1].get("buySellRatio", 1.0))

        return {
            "candles": candles,
            "funding_rate": funding_rate,
            "ls_ratio": ls_ratio,
            "taker_ratio": taker_ratio,
        }

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

    # ------------------------------------------------------------------
    # OHLCV feature computation
    # ------------------------------------------------------------------

    def _compute_ohlcv_features(self, candles: list[dict[str, Any]]) -> dict[str, float]:
        completed = candles[:-1]
        if len(completed) < 14:
            return {}

        prev = completed[-1]
        closes = [c["close"] for c in completed]
        highs  = [c["high"]  for c in completed]
        lows   = [c["low"]   for c in completed]
        vols   = [c["volume"] for c in completed]

        # Returns
        prev_return_1c  = (prev["close"] - prev["open"]) / max(prev["open"], 1e-8)
        base_3c  = completed[max(0, len(completed) - 3)]["close"]
        prev_return_3c  = (prev["close"] - base_3c)  / max(base_3c,  1e-8)
        base_6c  = completed[max(0, len(completed) - 6)]["close"]
        prev_return_6c  = (prev["close"] - base_6c)  / max(base_6c,  1e-8)
        base_12c = completed[max(0, len(completed) - 12)]["close"]
        prev_return_12c = (prev["close"] - base_12c) / max(base_12c, 1e-8)
        base_24c = completed[max(0, len(completed) - 24)]["close"]
        prev_return_24c = (prev["close"] - base_24c) / max(base_24c, 1e-8)
        base_48c = completed[max(0, len(completed) - 48)]["close"]
        prev_return_48c = (prev["close"] - base_48c) / max(base_48c, 1e-8)

        # RSI variants
        rsi_7   = _rsi(closes, period=7)
        rsi_14  = _rsi(closes, period=14)
        rsi_21  = _rsi(closes, period=21)

        # Volume ratio variants
        def _vol_ratio(window: int) -> float:
            avg = sum(vols[-window:]) / max(len(vols[-window:]), 1)
            return _clamp(math.log(max(prev["volume"], 1e-8) / max(avg, 1e-8)), -3.0, 3.0)

        # Realized volatility variants
        def _realized_vol(window: int) -> float:
            if len(closes) >= window + 1:
                rets = [(closes[i] - closes[i - 1]) / max(closes[i - 1], 1e-8) for i in range(-window, 0)]
                mean_r = sum(rets) / len(rets)
                return _clamp(math.sqrt(sum((r - mean_r) ** 2 for r in rets) / len(rets)) * 100, 0.0, 5.0)
            return 0.0

        # Bollinger Band variants
        def _bb_pos(window: int) -> float:
            bb_closes = closes[-window:]
            if len(bb_closes) < 2:
                return 0.0
            bb_mean = sum(bb_closes) / len(bb_closes)
            bb_std  = math.sqrt(sum((c - bb_mean) ** 2 for c in bb_closes) / len(bb_closes))
            upper = bb_mean + 2 * bb_std
            lower = bb_mean - 2 * bb_std
            bw = max(upper - lower, 1e-8)
            return _clamp((prev["close"] - lower) / bw, 0.0, 1.0) - 0.5

        # MACD
        def _ema(data: list[float], period: int) -> float:
            alpha = 2.0 / (period + 1)
            val = data[0]
            for d in data[1:]:
                val = alpha * d + (1 - alpha) * val
            return val
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_val = _clamp((ema12 - ema26) / max(closes[-1], 1e-8), -0.05, 0.05)

        # Stochastic %K (14-period)
        lo14 = min(lows[-14:]) if len(lows) >= 14 else min(lows)
        hi14 = max(highs[-14:]) if len(highs) >= 14 else max(highs)
        stoch_k = _clamp((closes[-1] - lo14) / max(hi14 - lo14, 1e-8), 0.0, 1.0) - 0.5

        # ATR ratio (14-period)
        tr_vals = []
        for k in range(max(1, len(completed) - 14), len(completed)):
            tr = max(
                highs[k] - lows[k],
                abs(highs[k] - closes[k - 1]),
                abs(lows[k]  - closes[k - 1]),
            )
            tr_vals.append(tr)
        atr = sum(tr_vals) / max(len(tr_vals), 1)
        atr_ratio = _clamp(atr / max(closes[-1], 1e-8) * 100, 0.0, 5.0)

        # Hour of day (cyclic)
        now_utc = datetime.now(UTC)
        hour_frac = now_utc.hour + now_utc.minute / 60.0
        f_hour_sin = math.sin(2 * math.pi * hour_frac / 24)
        f_hour_cos = math.cos(2 * math.pi * hour_frac / 24)

        # Weekday (cyclic)
        dow = now_utc.weekday()  # 0=Mon
        f_weekday_sin = math.sin(2 * math.pi * dow / 7)
        f_weekday_cos = math.cos(2 * math.pi * dow / 7)

        # Return ALL possible features — the model artifact selects which ones it needs
        return {
            # Returns (all lags the training script might use)
            "f_btc_return_1c":         _clamp(prev_return_1c,  -0.30, 0.30),
            "f_btc_return_3c":         _clamp(prev_return_3c,  -0.30, 0.30),
            "f_btc_return_6c":         _clamp(prev_return_6c,  -0.30, 0.30),
            "f_btc_return_12c":        _clamp(prev_return_12c, -0.30, 0.30),
            "f_btc_return_24c":        _clamp(prev_return_24c, -0.30, 0.30),
            "f_btc_return_48c":        _clamp(prev_return_48c, -0.30, 0.30),
            # Legacy feature names (v1 compat)
            "f_btc_prev_return_1c":    _clamp(prev_return_1c,  -0.10, 0.10),
            "f_btc_prev_return_3c":    _clamp(prev_return_3c,  -0.15, 0.15),
            "f_btc_prev_return_12c":   _clamp(prev_return_12c, -0.20, 0.20),
            "f_btc_prev_return_48c":   _clamp(prev_return_48c, -0.30, 0.30),
            # RSI variants
            "f_btc_rsi_7":             _clamp((rsi_7  - 0.5) * 2.0, -1.0, 1.0),
            "f_btc_rsi_14":            _clamp((rsi_14 - 0.5) * 2.0, -1.0, 1.0),
            "f_btc_rsi_21":            _clamp((rsi_21 - 0.5) * 2.0, -1.0, 1.0),
            # Volume ratio variants
            "f_btc_vol_ratio_ma10":    _vol_ratio(10),
            "f_btc_vol_ratio_ma14":    _vol_ratio(14),
            "f_btc_vol_ratio_ma20":    _vol_ratio(20),
            "f_btc_volume_ratio":      _vol_ratio(20),  # legacy alias
            # Realized volatility variants
            "f_btc_volatility_6c":     _realized_vol(6),
            "f_btc_volatility_8c":     _realized_vol(8),
            "f_btc_volatility_12c":    _realized_vol(12),
            "f_btc_volatility_24c":    _realized_vol(24),
            "f_btc_volatility_48c":    _realized_vol(48),
            # Bollinger Band variants
            "f_btc_bb_pos_10":         _bb_pos(10),
            "f_btc_bb_pos_14":         _bb_pos(14),
            "f_btc_bb_pos_20":         _bb_pos(20),
            "f_btc_bb_pos_30":         _bb_pos(30),
            "f_btc_bb_pos_48":         _bb_pos(48),
            "f_btc_bb_position":       _bb_pos(20),  # legacy alias
            # MACD
            "f_btc_macd":              macd_val,
            # Stochastic
            "f_btc_stochastic_k":      stoch_k,
            # ATR
            "f_btc_atr_ratio":         atr_ratio,
            # Time features
            "f_decision_hour_utc_sin": f_hour_sin,
            "f_decision_hour_utc_cos": f_hour_cos,
            "f_decision_weekday_sin":  f_weekday_sin,
            "f_decision_weekday_cos":  f_weekday_cos,
        }

    # ------------------------------------------------------------------
    # Derivatives feature computation
    # ------------------------------------------------------------------

    def _compute_derivatives_features(self, data: dict[str, Any]) -> dict[str, float]:
        feats: dict[str, float] = {}

        fr = data.get("funding_rate")
        if fr is not None and math.isfinite(fr):
            feats["f_btc_funding_rate"] = _clamp(fr * 100, -3.0, 3.0)
        else:
            feats["f_btc_funding_rate"] = 0.0

        ls = data.get("ls_ratio")
        if ls is not None and math.isfinite(ls) and ls > 0:
            feats["f_btc_ls_ratio_log"] = _clamp(math.log(ls), -2.0, 2.0)
        else:
            feats["f_btc_ls_ratio_log"] = 0.0

        tk = data.get("taker_ratio")
        if tk is not None and math.isfinite(tk) and tk > 0:
            feats["f_btc_taker_ratio_log"] = _clamp(math.log(tk), -2.0, 2.0)
        else:
            feats["f_btc_taker_ratio_log"] = 0.0

        return feats
