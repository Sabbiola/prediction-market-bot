"""BTC price + derivatives feature enricher for Polymarket BTC Up/Down markets.

Fetches live data from Binance and third-party sources:
  1. BTC/USDT 5m candles (spot) -> OHLCV technicals          (Binance)
  2. Funding rate (futures) -> long/short crowd sentiment      (Binance Futures)
  3. Global long/short account ratio (futures)                 (Binance Futures)
  4. Taker buy/sell ratio (futures) -> aggressor flow          (Binance Futures)
  5. Open interest history                                     (Binance Futures)
  6. Order book depth snapshot                                 (Binance spot)
  7. Fear & Greed Index (daily)                               (Alternative.me)
  8. Coinbase BTC-USD spot price -> cross-exchange basis       (Coinbase)
  9. Kraken XBTUSD last trade price -> cross-exchange basis    (Kraken)

Total: 44 live features. Feature names MUST match scripts/btc_train_exhaustive.py.

Cross-exchange basis rationale:
  Coinbase/Kraken often LEAD Binance price by 1-30s during US hours
  (institutional flow, arbitrage latency). A positive basis (CB > Binance)
  indicates upward pressure as arbitrageurs push Binance price up.

HTTP policy (REC-05):
  All outbound calls go through an injected ``StructuredHttpClient`` whenever
  one is provided — that client enforces allowed-hosts policy, circuit-breaker
  backoff, retry/jitter and timeouts. The bare-urllib path is retained only as
  a fallback for ad-hoc use outside the bot runtime (e.g. scripts).
"""
from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _HttpClientLike(Protocol):
    """Minimal protocol we need from ``StructuredHttpClient`` without coupling.

    Keeping this as a Protocol avoids a hard import cycle between the agent
    package (``prediction_market_bot.agents``) and the infrastructure package
    (``prediction_market_bot.infrastructure.http_client``).
    """

    def fetch_json(self, *, source: str, url: str, use_cache: bool = ..., cache_ttl_sec: int | None = ..., timeout_sec: float | None = ...) -> Any: ...

_BTC_UPDOWN_PATTERNS = (
    "btc-up-or-down",
    "btc-updown",
    "bitcoin-up-or-down",
    "bitcoin-updown",
    "btc up or down",
    "bitcoin up or down",
    "btc up/down",
    "bitcoin up/down",
)

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
FAPI_BASE = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
N_CANDLES = 200  # 200 x 5m = ~16h lookback (enables RSI-14 on 1h resampled bars)
CACHE_TTL_SEC = 60  # re-fetch at most once per minute

# Third-party data sources (no auth required)
ALTME_FNG_URL = "https://api.alternative.me/fng/?limit=1"
COINBASE_SPOT_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"


# Slot anchor cache: once a slot has started its BTC reference price never changes.
# Key = slot_start ISO string, value = BTC open price (float).
_SLOT_ANCHOR_CACHE: dict[str, float] = {}


def _fetch_slot_anchor_btc(
    slot_start_iso: str,
    *,
    timeout_sec: float = 8.0,
    http_client: _HttpClientLike | None = None,
) -> float | None:
    """BTC open price at the start of a Polymarket 15-min slot.

    Fetches the Coinbase Exchange 1-minute candle whose timestamp matches
    slot_start and returns its open.  Result cached forever per slot_start_iso
    because the anchor is immutable once the slot opens.
    """
    cached = _SLOT_ANCHOR_CACHE.get(slot_start_iso)
    if cached is not None:
        return cached
    try:
        cleaned = slot_start_iso.replace("Z", "+00:00")
        slot_start = datetime.fromisoformat(cleaned)
        if slot_start.tzinfo is None:
            slot_start = slot_start.replace(tzinfo=UTC)
    except ValueError:
        return None
    end = slot_start + timedelta(minutes=2)
    url = (
        "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        f"?granularity=60"
        f"&start={slot_start.isoformat().replace('+00:00', 'Z')}"
        f"&end={end.isoformat().replace('+00:00', 'Z')}"
    )
    candles = _safe_json_get(url, timeout=timeout_sec, http_client=http_client, source="slot_anchor")
    if not isinstance(candles, list) or not candles:
        return None
    # Coinbase returns DESC: [[time, low, high, open, close, vol], ...]
    target_unix = int(slot_start.timestamp())
    chosen = None
    for c in candles:
        if isinstance(c, list) and len(c) >= 4 and int(c[0]) == target_unix:
            chosen = c
            break
    if chosen is None:
        chosen = sorted(candles, key=lambda x: x[0])[0]
    try:
        anchor = float(chosen[3])  # open
    except (ValueError, TypeError, IndexError):
        return None
    _SLOT_ANCHOR_CACHE[slot_start_iso] = anchor
    return anchor


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


def _safe_json_get(
    url: str,
    *,
    timeout: float = 8.0,
    http_client: _HttpClientLike | None = None,
    source: str = "btc_feature_enricher",
) -> Any:
    """Fetch JSON from URL, return None on error.

    When ``http_client`` is provided, routes through the shared
    ``StructuredHttpClient`` so allowed-hosts enforcement, circuit breakers and
    structured metrics are all honoured. Falls back to bare urllib only for
    ad-hoc out-of-runtime callers (scripts, tests).
    """
    if http_client is not None:
        try:
            response = http_client.fetch_json(
                source=source,
                url=url,
                use_cache=False,  # enricher has its own TTL cache, avoid double cache
                timeout_sec=timeout,
            )
            return getattr(response, "payload", None)
        except Exception as exc:
            logger.debug("btc_feature_enricher_http_client_error url=%s error=%s", url, exc)
            return None
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
        http_client: _HttpClientLike | None = None,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.n_candles = n_candles
        self.cache_ttl_sec = cache_ttl_sec
        self.timeout_sec = timeout_sec
        self._http_client = http_client
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

    def get_btc_spot(self) -> float | None:
        """Return the most recently cached Coinbase BTC/USD spot price."""
        return self._get_cached_data().get("cb_price")

    def get_slot_features(
        self,
        *,
        hours_to_resolution: float,
        market_updated_at: datetime,
        yes_price: float,
    ) -> dict[str, float]:
        """Slot-specific latency arbitrage features for BTC Up/Down 15-min markets.

        Three signals:
          f_slot_elapsed_frac  — fraction of the 15-min slot already elapsed [0, 1)
          f_btc_vs_anchor_pct  — (BTC_now - BTC_slot_anchor) / BTC_slot_anchor * 100
          f_poly_misprice      — Polymarket YES price minus sigmoid-implied fair value

        Returns an empty dict on any error so the prediction pipeline degrades gracefully.
        """
        try:
            now_utc = datetime.now(UTC)
            updated = market_updated_at if market_updated_at.tzinfo else market_updated_at.replace(tzinfo=UTC)
            slot_close = updated + timedelta(hours=float(hours_to_resolution))
            slot_start = slot_close - timedelta(minutes=15)
            slot_start_iso = slot_start.isoformat().replace("+00:00", "Z")

            elapsed_sec = (now_utc - slot_start).total_seconds()
            f_slot_elapsed_frac = _clamp(elapsed_sec / 900.0, 0.0, 1.0)

            btc_spot = self.get_btc_spot()
            anchor = _fetch_slot_anchor_btc(
                slot_start_iso,
                timeout_sec=self.timeout_sec,
                http_client=self._http_client,
            )

            feats: dict[str, float] = {"f_slot_elapsed_frac": f_slot_elapsed_frac}

            if btc_spot is None or anchor is None or anchor <= 0:
                return feats

            raw_delta_pct = (btc_spot - anchor) / anchor * 100.0
            f_btc_vs_anchor_pct = _clamp(raw_delta_pct, -3.0, 3.0)
            feats["f_btc_vs_anchor_pct"] = f_btc_vs_anchor_pct

            # Sigmoid-implied fair YES given BTC position vs anchor.
            # Calibration: ±0.3 % move → ~95 % certainty for the slot winner.
            implied_fair_yes = 1.0 / (1.0 + math.exp(-raw_delta_pct * 10.0))
            f_poly_misprice = _clamp(float(yes_price) - implied_fair_yes, -0.5, 0.5)
            feats["f_poly_misprice"] = f_poly_misprice

            return feats
        except Exception as exc:
            logger.warning("btc_slot_features_error: %s", exc)
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
        """Fetch candles + all derivatives in parallel-ish sequence (~300ms total)."""
        candles = self._fetch_candles()

        # Funding rate — latest entry
        funding_raw = _safe_json_get(
            f"{FAPI_BASE}/fapi/v1/fundingRate?symbol={self.symbol}&limit=1",
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        funding_rate = None
        if isinstance(funding_raw, list) and funding_raw:
            funding_rate = float(funding_raw[-1].get("fundingRate", 0))

        # Long/short account ratio — latest 5m
        ls_raw = _safe_json_get(
            f"{FAPI_BASE}/futures/data/globalLongShortAccountRatio?symbol={self.symbol}&period=5m&limit=1",
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        ls_ratio = None
        if isinstance(ls_raw, list) and ls_raw:
            ls_ratio = float(ls_raw[-1].get("longShortRatio", 1.0))

        # Taker buy/sell ratio — latest 5m
        taker_raw = _safe_json_get(
            f"{FAPI_BASE}/futures/data/takerlongshortRatio?symbol={self.symbol}&period=5m&limit=1",
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        taker_ratio = None
        if isinstance(taker_raw, list) and taker_raw:
            taker_ratio = float(taker_raw[-1].get("buySellRatio", 1.0))

        # Open interest — latest 5m + previous (to compute change)
        oi_raw = _safe_json_get(
            f"{FAPI_BASE}/futures/data/openInterestHist?symbol={self.symbol}&period=5m&limit=2",
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        oi_current = None
        oi_prev = None
        if isinstance(oi_raw, list) and len(oi_raw) >= 1:
            oi_current = float(oi_raw[-1].get("sumOpenInterest", 0))
        if isinstance(oi_raw, list) and len(oi_raw) >= 2:
            oi_prev = float(oi_raw[-2].get("sumOpenInterest", 0))

        # Order book imbalance — spot depth (top 20 levels)
        ob_raw = _safe_json_get(
            f"https://api.binance.com/api/v3/depth?symbol={self.symbol}&limit=20",
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        ob_imbalance = None
        ob_spread_bps = None
        if isinstance(ob_raw, dict):
            bids = ob_raw.get("bids", [])
            asks = ob_raw.get("asks", [])
            bid_vol = sum(float(b[1]) for b in bids)
            ask_vol = sum(float(a[1]) for a in asks)
            total_vol = bid_vol + ask_vol
            if total_vol > 0:
                ob_imbalance = (bid_vol - ask_vol) / total_vol
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                if best_bid > 0:
                    ob_spread_bps = (best_ask - best_bid) / best_bid * 10000

        # Fear & Greed Index (daily signal, Alternative.me)
        fng_raw = _safe_json_get(
            ALTME_FNG_URL,
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        fng_value: int | None = None
        if isinstance(fng_raw, dict):
            entries = fng_raw.get("data", [])
            if entries:
                try:
                    fng_value = int(entries[0].get("value", 50))
                except (TypeError, ValueError):
                    pass

        # Coinbase BTC-USD spot price (cross-exchange basis)
        cb_raw = _safe_json_get(
            COINBASE_SPOT_URL,
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        cb_price: float | None = None
        if isinstance(cb_raw, dict):
            try:
                amount = cb_raw.get("data", {}).get("amount") or 0
                cb_price = float(amount) if amount else None
            except (TypeError, ValueError):
                pass

        # Kraken XBTUSD last trade price (cross-exchange basis)
        kr_raw = _safe_json_get(
            KRAKEN_TICKER_URL,
            timeout=self.timeout_sec,
            http_client=self._http_client,
        )
        kr_price: float | None = None
        if isinstance(kr_raw, dict) and not kr_raw.get("error"):
            try:
                for ticker in kr_raw.get("result", {}).values():
                    kr_price = float(ticker["c"][0])  # last trade closed
                    break
            except (KeyError, IndexError, TypeError, ValueError):
                pass

        return {
            "candles":      candles,
            "funding_rate": funding_rate,
            "ls_ratio":     ls_ratio,
            "taker_ratio":  taker_ratio,
            "oi_current":   oi_current,
            "oi_prev":      oi_prev,
            "ob_imbalance": ob_imbalance,
            "ob_spread_bps": ob_spread_bps,
            "fng_value":    fng_value,
            "cb_price":     cb_price,
            "kr_price":     kr_price,
        }

    def _fetch_candles(self) -> list[dict[str, Any]]:
        url = (
            f"{BINANCE_KLINES_URL}"
            f"?symbol={self.symbol}&interval={self.interval}&limit={self.n_candles}"
        )
        raw: Any
        if self._http_client is not None:
            raw = _safe_json_get(
                url,
                timeout=self.timeout_sec,
                http_client=self._http_client,
                source="btc_feature_enricher.klines",
            )
            if raw is None:
                raise RuntimeError("btc_feature_enricher_klines_fetch_failed")
        else:
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
        # Use the open timestamp of the last COMPLETED candle — matches training
        # which uses candles[idx]["open_time_ms"]/1000, not datetime.now().
        last_completed = completed[-1] if completed else candles[-1]
        open_time_sec = last_completed.get("open_time_ms", 0) / 1000
        hour_frac = (open_time_sec % 86400) / 3600
        f_hour_sin = math.sin(2 * math.pi * hour_frac / 24)
        f_hour_cos = math.cos(2 * math.pi * hour_frac / 24)

        # Weekday (cyclic) — derived from same candle timestamp for consistency
        dow = int(open_time_sec // 86400 + 4) % 7  # 0=Mon (Unix epoch was Thu, +4 aligns)
        f_weekday_sin = math.sin(2 * math.pi * dow / 7)
        f_weekday_cos = math.cos(2 * math.pi * dow / 7)

        # Gap open: (current candle open - prev close) / prev close
        # Captures overnight / inter-bar gap visible at candle start
        _gap_raw = (candles[-1]["open"] - prev["close"]) / max(prev["close"], 1e-8)
        f_btc_gap_open = _clamp(_gap_raw, -0.05, 0.05)

        # RSI(14) on 1h bars resampled from 5m completed candles.
        # Takes the close of every 12th candle stepping backwards from the most recent.
        # With N_CANDLES=200: ~16 1h bars available → RSI(14) feasible.
        _closes_1h: list[float] = []
        _i = len(completed) - 1
        while _i >= 0:
            _closes_1h.insert(0, completed[_i]["close"])
            _i -= 12
        _rsi_1h_raw = _rsi(_closes_1h, period=14)  # returns 0.5 if insufficient data
        f_btc_rsi_1h = _clamp((_rsi_1h_raw - 0.5) * 2.0, -1.0, 1.0)

        # Return ALL possible features — the model artifact selects which ones it needs
        return {
            # Returns (all lags the training script might use)
            "f_btc_return_1c":         _clamp(prev_return_1c,  -0.30, 0.30),
            "f_btc_return_3c":         _clamp(prev_return_3c,  -0.30, 0.30),
            "f_btc_return_6c":         _clamp(prev_return_6c,  -0.30, 0.30),
            "f_btc_return_12c":        _clamp(prev_return_12c, -0.30, 0.30),
            "f_btc_return_24c":        _clamp(prev_return_24c, -0.30, 0.30),
            "f_btc_return_48c":        _clamp(prev_return_48c, -0.30, 0.30),
            # btc-v2 prev_return names (match btc_train_v2.py FEATURE_NAMES exactly)
            "f_btc_prev_return_1c":    _clamp(prev_return_1c,  -0.30, 0.30),
            "f_btc_prev_return_3c":    _clamp(prev_return_3c,  -0.30, 0.30),
            "f_btc_prev_return_6c":    _clamp(prev_return_6c,  -0.30, 0.30),
            "f_btc_prev_return_12c":   _clamp(prev_return_12c, -0.30, 0.30),
            "f_btc_prev_return_24c":   _clamp(prev_return_24c, -0.30, 0.30),
            "f_btc_prev_return_48c":   _clamp(prev_return_48c, -0.30, 0.30),
            # RSI variants
            "f_btc_rsi_7":             _clamp((rsi_7  - 0.5) * 2.0, -1.0, 1.0),
            "f_btc_rsi_14":            _clamp((rsi_14 - 0.5) * 2.0, -1.0, 1.0),
            "f_btc_rsi_21":            _clamp((rsi_21 - 0.5) * 2.0, -1.0, 1.0),
            # Volume ratio variants
            "f_btc_vol_ratio_ma10":    _vol_ratio(10),
            "f_btc_vol_ratio_ma14":    _vol_ratio(14),
            "f_btc_vol_ratio_ma20":    _vol_ratio(20),
            "f_btc_volume_ratio":      _vol_ratio(20),  # matches FEATURE_NAMES
            # Realized volatility variants
            "f_btc_volatility_6c":     _realized_vol(6),
            "f_btc_volatility_8c":     _realized_vol(8),
            "f_btc_volatility_12c":    _realized_vol(12),
            "f_btc_volatility_24c":    _realized_vol(24),
            "f_btc_volatility_48c":    _realized_vol(48),
            # Bollinger Band — both naming conventions (pos = v1, position = v2)
            "f_btc_bb_pos_10":         _bb_pos(10),
            "f_btc_bb_pos_14":         _bb_pos(14),
            "f_btc_bb_pos_20":         _bb_pos(20),
            "f_btc_bb_pos_30":         _bb_pos(30),
            "f_btc_bb_pos_48":         _bb_pos(48),
            "f_btc_bb_position":       _bb_pos(20),   # legacy alias (v1)
            "f_btc_bb_position_10":    _bb_pos(10),   # v2 name (matches FEATURE_NAMES)
            "f_btc_bb_position_20":    _bb_pos(20),   # v2 name (matches FEATURE_NAMES)
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
            # Cross-timeframe & gap (v2)
            "f_btc_gap_open":          f_btc_gap_open,
            "f_btc_rsi_1h":            f_btc_rsi_1h,
            # Coinbase lead-lag (zero when CB data not fetched live; model trained with CB data)
            "f_btc_cb_return_1c":      0.0,
            "f_btc_cb_return_3c":      0.0,
            "f_btc_cb_bn_spread_1c":   0.0,
            "f_btc_cb_bn_spread_3c":   0.0,
            "f_btc_cb_vol_dominance":  0.0,
            "f_btc_cb_momentum_lead":  0.0,
        }

    # ------------------------------------------------------------------
    # Derivatives feature computation
    # ------------------------------------------------------------------

    def _compute_derivatives_features(self, data: dict[str, Any]) -> dict[str, float]:
        feats: dict[str, float] = {}

        # Funding rate (scaled x100, clipped to [-3, 3])
        fr = data.get("funding_rate")
        feats["f_btc_funding_rate"] = (
            _clamp(fr * 100, -3.0, 3.0) if fr is not None and math.isfinite(fr) else 0.0
        )

        # Long/short ratio (log-scaled, centered at 0)
        ls = data.get("ls_ratio")
        feats["f_btc_ls_ratio_log"] = (
            _clamp(math.log(ls), -2.0, 2.0) if ls is not None and math.isfinite(ls) and ls > 0 else 0.0
        )

        # Taker buy/sell ratio (log-scaled, centered at 0)
        tk = data.get("taker_ratio")
        feats["f_btc_taker_ratio_log"] = (
            _clamp(math.log(tk), -2.0, 2.0) if tk is not None and math.isfinite(tk) and tk > 0 else 0.0
        )

        # Open interest percentage change vs previous 5m candle
        oi_cur  = data.get("oi_current")
        oi_prev = data.get("oi_prev")
        if oi_cur is not None and oi_prev is not None and oi_prev > 0:
            feats["f_btc_oi_change_pct"] = _clamp((oi_cur - oi_prev) / oi_prev * 100, -5.0, 5.0)
        else:
            feats["f_btc_oi_change_pct"] = 0.0

        # Order book imbalance [-1, 1]: positive = more bids
        ob = data.get("ob_imbalance")
        feats["f_btc_ob_imbalance"] = (
            _clamp(ob, -1.0, 1.0) if ob is not None and math.isfinite(ob) else 0.0
        )

        # Spread in bps (normalised: divide by 10 to get ~[0, 1] range for BTC)
        sp = data.get("ob_spread_bps")
        feats["f_btc_ob_spread_bps"] = (
            _clamp(sp / 10.0, 0.0, 5.0) if sp is not None and math.isfinite(sp) else 0.0
        )

        # Fear & Greed Index [0, 1]: 0=extreme fear, 1=extreme greed.
        # At extremes (< 0.2 or > 0.8) acts as mean-reversion signal.
        # Default 0.5 (neutral) when unavailable.
        fng = data.get("fng_value")
        feats["f_btc_fear_greed"] = (
            _clamp(fng / 100.0, 0.0, 1.0) if fng is not None else 0.5
        )

        # Cross-exchange basis vs Binance (latest candle close as reference price).
        # Positive basis = Coinbase/Kraken trading above Binance → arbitrage pressure
        # pushes Binance up → mild bullish signal. Clipped ±30 bps.
        candles = data.get("candles", [])
        binance_ref = candles[-1]["close"] if candles else 0.0

        cb = data.get("cb_price")
        feats["f_btc_cb_basis_bps"] = (
            _clamp((cb - binance_ref) / binance_ref * 10_000, -30.0, 30.0)
            if cb is not None and cb > 0 and binance_ref > 0
            else 0.0
        )

        kr = data.get("kr_price")
        feats["f_btc_kraken_basis_bps"] = (
            _clamp((kr - binance_ref) / binance_ref * 10_000, -30.0, 30.0)
            if kr is not None and kr > 0 and binance_ref > 0
            else 0.0
        )

        return feats
