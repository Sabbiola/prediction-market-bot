from __future__ import annotations

import json
import time as _time_module
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable, Mapping

from prediction_market_bot.domain.enums import MarketStatus
from prediction_market_bot.domain.models import MarketSnapshot
from prediction_market_bot.interfaces import MarketDataPort, PersistencePort
from prediction_market_bot.infrastructure.http_client import (
    HttpClientError,
    HttpErrorMetadata,
    StructuredHttpClient,
)


@dataclass(slots=True, frozen=True)
class LiveMarketBatch:
    run_id: str
    fetched_count: int
    normalized_count: int
    stale_count: int
    invalid_count: int
    retries_used: int
    cache_hit: bool
    fetch_duration_ms: float
    total_duration_ms: float
    snapshots: tuple[MarketSnapshot, ...]


class PolymarketReadOnlyMarketDataAdapter(MarketDataPort):
    """Read-only live market data adapter with normalization and persistence."""

    def __init__(
        self,
        *,
        endpoint_url: str = "https://gamma-api.polymarket.com/markets",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        max_staleness_seconds: int = 900,
        default_limit: int = 50,
        cache_ttl_sec: int = 0,
        headers: Mapping[str, str] | None = None,
        api_key: str = "",
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        require_api_key: bool = False,
        persistence: PersistencePort | None = None,
        now_fn: Callable[[], datetime] | None = None,
        http_client: StructuredHttpClient | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec
        self.retry_jitter_sec = retry_jitter_sec
        self.max_staleness_seconds = max_staleness_seconds
        self.default_limit = max(default_limit, 1)
        self.cache_ttl_sec = max(cache_ttl_sec, 0)
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        self.api_key = api_key.strip()
        self.api_key_header = api_key_header.strip()
        self.api_key_prefix = api_key_prefix
        self.require_api_key = require_api_key
        self.persistence = persistence
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.http_client = http_client or StructuredHttpClient(
            user_agent="prediction-market-bot/0.1",
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            cache_ttl_sec=self.cache_ttl_sec,
            now_fn=self.now_fn,
        )

    def list_active_markets(self) -> list[MarketSnapshot]:
        return list(self.fetch_batch(limit=self.default_limit).snapshots)

    def fetch_batch(self, *, run_id: str | None = None, limit: int = 50) -> LiveMarketBatch:
        effective_run_id = run_id or f"live-fetch-{self.now_fn().strftime('%Y%m%d%H%M%S')}"
        total_started = perf_counter()
        try:
            payload, retries_used, cache_hit, fetch_duration_ms = self._fetch_payload(limit=limit)
        except HttpClientError as exc:
            self._persist_http_error(effective_run_id, exc.metadata)
            raise RuntimeError(
                "failed_to_fetch_live_market_data "
                f"classification={exc.metadata.classification} reason={exc.metadata.reason_code} "
                f"message={exc.metadata.message}"
            ) from exc
        raw_markets, malformed_reason = self._extract_markets(payload)
        if malformed_reason:
            self._persist_source_failure(
                effective_run_id,
                classification="malformed_payload",
                reason_code=malformed_reason,
                message="live_market_payload_malformed",
                source="polymarket_live_market_data",
            )
            raise RuntimeError(
                "failed_to_fetch_live_market_data "
                f"classification=malformed_payload reason={malformed_reason} "
                "message=live_market_payload_malformed"
            )

        snapshots: list[MarketSnapshot] = []
        stale_count = 0
        invalid_count = 0
        now = self.now_fn()

        for raw in raw_markets:
            normalized, stale_reason, invalid_reason = self._normalize_market(raw, now=now)
            self._persist_raw(effective_run_id, raw, stale_reason=stale_reason, invalid_reason=invalid_reason)

            if stale_reason is not None:
                stale_count += 1
                self._persist_source_failure(
                    effective_run_id,
                    classification="stale_data",
                    reason_code=stale_reason,
                    message="live_market_snapshot_stale",
                    source="polymarket_live_market_data",
                    payload={"market_id": self._first_str(raw, ("id", "market_id", "conditionId", "slug")) or ""},
                )
                continue
            if invalid_reason is not None:
                invalid_count += 1
                self._persist_source_failure(
                    effective_run_id,
                    classification="malformed_payload",
                    reason_code=invalid_reason,
                    message="live_market_snapshot_invalid",
                    source="polymarket_live_market_data",
                    payload={"market_id": self._first_str(raw, ("id", "market_id", "conditionId", "slug")) or ""},
                )
                continue
            if normalized is None:
                invalid_count += 1
                self._persist_source_failure(
                    effective_run_id,
                    classification="malformed_payload",
                    reason_code="normalization_returned_none",
                    message="live_market_snapshot_invalid",
                    source="polymarket_live_market_data",
                )
                continue

            snapshots.append(normalized)
            self._persist_normalized(effective_run_id, normalized)

        if self.persistence:
            self.persistence.write_run_event(
                effective_run_id,
                "live_market_fetch_end",
                {
                    "run_id": effective_run_id,
                    "fetched_count": len(raw_markets),
                    "normalized_count": len(snapshots),
                    "stale_count": stale_count,
                    "invalid_count": invalid_count,
                    "retries_used": retries_used,
                    "cache_hit": cache_hit,
                    "fetch_duration_ms": round(fetch_duration_ms, 3),
                    "total_duration_ms": round(max((perf_counter() - total_started) * 1000.0, 0.0), 3),
                    "endpoint_url": self.endpoint_url,
                },
            )

        return LiveMarketBatch(
            run_id=effective_run_id,
            fetched_count=len(raw_markets),
            normalized_count=len(snapshots),
            stale_count=stale_count,
            invalid_count=invalid_count,
            retries_used=retries_used,
            cache_hit=cache_hit,
            fetch_duration_ms=round(fetch_duration_ms, 3),
            total_duration_ms=round(max((perf_counter() - total_started) * 1000.0, 0.0), 3),
            snapshots=tuple(snapshots),
        )

    def _fetch_payload(self, *, limit: int) -> tuple[Any, int, bool, float]:
        started = perf_counter()
        response = self.http_client.fetch_json(
            source="polymarket_live_market_data",
            url=self.endpoint_url,
            query_params=self._build_query_params(limit=limit),
            headers=self.headers,
            use_cache=self.cache_ttl_sec > 0,
            cache_ttl_sec=self.cache_ttl_sec,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            api_key=self.api_key,
            api_key_header=self.api_key_header,
            api_key_prefix=self.api_key_prefix,
            require_api_key=self.require_api_key,
        )
        fetch_duration_ms = max((perf_counter() - started) * 1000.0, 0.0)
        return response.payload, response.retries_used, response.cache_hit, fetch_duration_ms

    @staticmethod
    def _build_query_params(*, limit: int) -> dict[str, str]:
        return {"active": "true", "closed": "false", "limit": str(max(limit, 1))}

    @staticmethod
    def _extract_markets(payload: Any) -> tuple[list[Mapping[str, Any]], str | None]:
        extracted = []
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, Mapping):
                    continue
                if "markets" in row and isinstance(row["markets"], list):
                    for m in row["markets"]:
                        if isinstance(m, Mapping):
                            extracted.append(m)
                else:
                    extracted.append(row)
            return extracted, None
        if isinstance(payload, Mapping):
            markets = payload.get("markets")
            if isinstance(markets, list):
                return [row for row in markets if isinstance(row, Mapping)], None
            return [], "markets_field_missing_or_not_list"
        return [], "payload_not_list_or_mapping"

    def _normalize_market(
        self,
        raw: Mapping[str, Any],
        *,
        now: datetime,
    ) -> tuple[MarketSnapshot | None, str | None, str | None]:
        status = self._parse_status(raw)
        if status != MarketStatus.OPEN:
            return None, None, "market_not_open"

        updated_at = self._parse_timestamp(raw.get("updatedAt") or raw.get("updated_at") or raw.get("lastUpdated"))
        if updated_at is None:
            return None, None, "missing_updated_at"

        age_seconds = (now - updated_at).total_seconds()
        if age_seconds > self.max_staleness_seconds:
            return None, "stale_data", None

        market_id = self._first_str(raw, ("id", "market_id", "conditionId", "slug"))
        title = self._first_str(raw, ("question", "title", "name"))
        venue = self._first_str(raw, ("venue",), default="polymarket") or "polymarket"
        category = self._first_str(raw, ("category", "group", "topic"), default="general") or "general"
        yes_price = self._extract_yes_price(raw)

        if market_id is None or title is None or yes_price is None:
            return None, None, "missing_required_fields"

        liquidity_usd = self._first_float(raw, ("liquidity", "liquidityClob", "liquidity_num"), default=0.0)
        volume_24h_usd = self._first_float(raw, ("volume24hr", "volume24h", "volume"), default=0.0)
        if liquidity_usd is None:
            liquidity_usd = 0.0
        if volume_24h_usd is None:
            volume_24h_usd = 0.0
        spread_bps = self._extract_spread_bps(raw)
        hours_to_resolution = self._hours_to_resolution(raw, now=now)
        last_price_move_bps = self._extract_price_move_bps(raw)

        try:
            snapshot = MarketSnapshot.from_yes_price(
                market_id=market_id,
                venue=venue,
                title=title,
                yes_price=yes_price,
                liquidity_usd=liquidity_usd,
                volume_24h_usd=volume_24h_usd,
                spread_bps=spread_bps,
                hours_to_resolution=hours_to_resolution,
                last_price_move_bps=last_price_move_bps,
                category=category,
                status=status,
                updated_at=updated_at,
            )
            return snapshot, None, None
        except Exception:
            return None, None, "normalization_validation_error"

    @staticmethod
    def _parse_status(raw: Mapping[str, Any]) -> MarketStatus:
        if bool(raw.get("closed", False)):
            return MarketStatus.CLOSED
        if bool(raw.get("resolved", False)):
            return MarketStatus.RESOLVED
        if "active" in raw and not bool(raw.get("active")):
            return MarketStatus.HALTED
        status = raw.get("status")
        if isinstance(status, str):
            upper = status.strip().upper()
            if upper in {"OPEN", "ACTIVE"}:
                return MarketStatus.OPEN
            if upper in {"CLOSED"}:
                return MarketStatus.CLOSED
            if upper in {"RESOLVED"}:
                return MarketStatus.RESOLVED
        return MarketStatus.OPEN

    def _extract_yes_price(self, raw: Mapping[str, Any]) -> float | None:
        direct = self._first_float(raw, ("yes_price", "yesPrice", "price_yes"))
        if direct is not None:
            return self._clamp_probability(direct)

        outcomes = raw.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = None

        if isinstance(outcomes, list):
            named_yes = self._extract_yes_from_outcomes(outcomes)
            if named_yes is not None:
                return self._clamp_probability(named_yes)

        outcome_prices = raw.get("outcomePrices")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except json.JSONDecodeError:
                outcome_prices = None
        if isinstance(outcome_prices, list) and len(outcome_prices) >= 1:
            parsed = self._to_float(outcome_prices[0])
            if parsed is not None:
                return self._clamp_probability(parsed)
        return None

    def _extract_yes_from_outcomes(self, outcomes: list[Any]) -> float | None:
        for entry in outcomes:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name.strip().lower() == "yes":
                price = self._to_float(entry.get("price"))
                if price is not None:
                    return price
        if len(outcomes) >= 1:
            first = outcomes[0]
            if isinstance(first, Mapping):
                parsed = self._to_float(first.get("price"))
                if parsed is not None:
                    return parsed
        return None

    def _extract_spread_bps(self, raw: Mapping[str, Any]) -> int:
        spread = self._first_float(raw, ("spread_bps", "spreadBps"))
        if spread is not None:
            return max(int(round(spread)), 0)

        spread_raw = self._first_float(raw, ("spread",))
        if spread_raw is not None:
            if spread_raw <= 1.0:
                return max(int(round(spread_raw * 10_000)), 0)
            return max(int(round(spread_raw)), 0)

        best_bid = self._first_float(raw, ("bestBid", "best_bid"))
        best_ask = self._first_float(raw, ("bestAsk", "best_ask"))
        if best_bid is not None and best_ask is not None:
            return max(int(round(abs(best_ask - best_bid) * 10_000)), 0)
        return 0

    def _hours_to_resolution(self, raw: Mapping[str, Any], *, now: datetime) -> float:
        end_date = self._parse_timestamp(raw.get("endDate") or raw.get("endDateIso") or raw.get("endDateISO"))
        if end_date is None:
            return 24.0
        delta_hours = (end_date - now).total_seconds() / 3600.0
        return max(round(delta_hours, 4), 0.0)

    def _extract_price_move_bps(self, raw: Mapping[str, Any]) -> int:
        move = self._first_float(raw, ("priceChange24h", "price_change_24h"))
        if move is None:
            return 0
        if abs(move) <= 1.0:
            return int(round(move * 10_000))
        return int(round(move))

    def _persist_raw(
        self,
        run_id: str,
        raw: Mapping[str, Any],
        *,
        stale_reason: str | None,
        invalid_reason: str | None,
    ) -> None:
        if not self.persistence:
            return
        self.persistence.write_artifact(
            run_id,
            "raw_market_snapshots",
            {
                "stale_reason": stale_reason,
                "invalid_reason": invalid_reason,
                "payload": dict(raw),
            },
        )

    def _persist_normalized(self, run_id: str, snapshot: MarketSnapshot) -> None:
        if not self.persistence:
            return
        self.persistence.write_artifact(run_id, "normalized_market_snapshots", snapshot.model_dump(mode="json"))

    def _persist_http_error(self, run_id: str, metadata: HttpErrorMetadata) -> None:
        if not self.persistence:
            return
        payload = metadata.to_dict()
        self.persistence.write_artifact(run_id, "http_error_metadata", payload)
        self.persistence.write_run_event(run_id, "live_market_fetch_failed", payload)
        self._persist_source_failure(
            run_id,
            classification=metadata.classification,
            reason_code=metadata.reason_code,
            message=metadata.message,
            source=metadata.source,
            payload={
                "status_code": metadata.status_code,
                "retryable": metadata.retryable,
                "error_type": metadata.error_type,
            },
        )

    def _persist_source_failure(
        self,
        run_id: str,
        *,
        classification: str,
        reason_code: str,
        message: str,
        source: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.persistence:
            return
        failure_payload: dict[str, Any] = {
            "source": source,
            "classification": classification,
            "reason_code": reason_code,
            "message": message,
            "timestamp": self.now_fn().isoformat(),
        }
        if payload:
            failure_payload["details"] = dict(payload)
        self.persistence.write_artifact(run_id, "source_failures", failure_payload)
        self.persistence.write_run_event(run_id, "source_failure", failure_payload)

    @staticmethod
    def _first_str(raw: Mapping[str, Any], keys: tuple[str, ...], default: str | None = None) -> str | None:
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return default

    def _first_float(
        self,
        raw: Mapping[str, Any],
        keys: tuple[str, ...],
        default: float | None = None,
    ) -> float | None:
        for key in keys:
            parsed = self._to_float(raw.get(key))
            if parsed is not None:
                return parsed
        return default

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

    @staticmethod
    def _clamp_probability(value: float) -> float:
        return min(max(value, 0.0), 1.0)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class BtcUpDown15mEventsAdapter(PolymarketReadOnlyMarketDataAdapter):
    """Fetches BTC Up/Down 15m markets via Gamma /events endpoint.

    Queries events opened in the last 2h and filters for ticker prefix
    'btc-updown-15m-', then flattens their nested markets list. This avoids
    downloading 50+ general markets just to find 1-2 BTC 15m slots and works
    reliably even when BTC slots don't appear in the top-N markets by liquidity.
    """

    _EVENTS_URL = "https://gamma-api.polymarket.com/events"
    _BTC_15M_TICKER_PREFIX = "btc-updown-15m-"
    _LOOKBACK_SEC = 7200  # 2h window — catches current slot + next 3-4 slots

    def _fetch_payload(self, *, limit: int) -> tuple[Any, int, bool, float]:
        started = perf_counter()
        start_date_min = int(_time_module.time()) - self._LOOKBACK_SEC
        response = self.http_client.fetch_json(
            source="polymarket_live_market_data",
            url=self._EVENTS_URL,
            query_params={"start_date_min": str(start_date_min), "limit": str(max(limit, 30))},
            headers=self.headers,
            use_cache=False,
            cache_ttl_sec=0,
            timeout_sec=self.timeout_sec,
            max_retries=self.max_retries,
            retry_backoff_sec=self.retry_backoff_sec,
            retry_jitter_sec=self.retry_jitter_sec,
            api_key=self.api_key,
            api_key_header=self.api_key_header,
            api_key_prefix=self.api_key_prefix,
            require_api_key=self.require_api_key,
        )
        fetch_duration_ms = max((perf_counter() - started) * 1000.0, 0.0)
        payload = response.payload
        if isinstance(payload, list):
            markets: list[Any] = []
            for event in payload:
                if not isinstance(event, dict):
                    continue
                ticker = str(event.get("ticker", "")).lower()
                if not ticker.startswith(self._BTC_15M_TICKER_PREFIX):
                    continue
                for m in event.get("markets", []):
                    if isinstance(m, dict):
                        markets.append(m)
            payload = markets
        return payload, response.retries_used, response.cache_hit, fetch_duration_ms
