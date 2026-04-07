from __future__ import annotations

import time
from datetime import date
from typing import Any, Mapping

from prediction_market_bot.infrastructure import HttpClientError, StructuredHttpClient

from .models import HistoricalMarketPage


class HistoricalResolvedMarketProvider:
    def __init__(
        self,
        *,
        http_client: StructuredHttpClient,
        markets_endpoint_url: str,
        events_endpoint_url: str,
        snapshots_endpoint_url: str,
        orderbook_endpoint_url: str,
        trades_endpoint_url: str,
        resolutions_endpoint_url: str,
        include_orderbook: bool,
        include_trades: bool,
        page_size: int,
        throttle_sec: float,
    ) -> None:
        self.http_client = http_client
        self.markets_endpoint_url = markets_endpoint_url
        self.events_endpoint_url = events_endpoint_url
        self.snapshots_endpoint_url = snapshots_endpoint_url
        self.orderbook_endpoint_url = orderbook_endpoint_url
        self.trades_endpoint_url = trades_endpoint_url
        self.resolutions_endpoint_url = resolutions_endpoint_url
        self.include_orderbook = include_orderbook
        self.include_trades = include_trades
        self.page_size = max(page_size, 1)
        self.throttle_sec = max(throttle_sec, 0.0)

    def fetch_resolved_markets_page(
        self,
        *,
        cursor: str | None,
        page_size: int | None,
        date_from: date | None,
        date_to: date | None,
    ) -> HistoricalMarketPage:
        effective_page_size = max(page_size if page_size is not None else self.page_size, 1)
        # Gamma API uses numeric offset; cursor encodes next-page offset as decimal string.
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        params: dict[str, str] = {
            "closed": "true",
            "resolved": "true",
            "limit": str(effective_page_size),
        }
        if offset > 0:
            params["offset"] = str(offset)
        if date_from is not None:
            params["date_from"] = date_from.isoformat()
        if date_to is not None:
            params["date_to"] = date_to.isoformat()
        response = self._fetch(
            source="historical_resolved_markets",
            url=self.markets_endpoint_url,
            query_params=params,
        )
        payload = response.payload
        if isinstance(payload, list):
            # Gamma API returns a bare list — synthesise a cursor for the next page.
            markets = tuple(row for row in payload if isinstance(row, Mapping))
            next_cursor = str(offset + effective_page_size) if markets else None
            return HistoricalMarketPage(markets=markets, next_cursor=next_cursor, retries_used=response.retries_used)
        if not isinstance(payload, Mapping):
            raise RuntimeError("historical_markets_payload_not_list_or_mapping")
        raw_markets = payload.get("markets")
        if not isinstance(raw_markets, list):
            raw_markets = payload.get("data")
        if not isinstance(raw_markets, list):
            raise RuntimeError("historical_markets_payload_missing_markets_list")
        next_cursor_raw = payload.get("next_cursor") or payload.get("nextCursor")
        next_cursor = str(next_cursor_raw).strip() if isinstance(next_cursor_raw, str) and next_cursor_raw.strip() else None
        markets = tuple(row for row in raw_markets if isinstance(row, Mapping))
        return HistoricalMarketPage(markets=markets, next_cursor=next_cursor, retries_used=response.retries_used)

    def fetch_event_metadata(self, *, event_id: str) -> Mapping[str, Any] | None:
        event_id = event_id.strip()
        if not event_id:
            return None
        payload = self._fetch_optional(
            source="historical_event_metadata",
            url=self._interpolate_url(self.events_endpoint_url, event_id),
            query_params={"event_id": event_id} if "{event_id}" not in self.events_endpoint_url else None,
        )
        if isinstance(payload, Mapping):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], Mapping):
            return payload[0]
        return None

    def fetch_market_snapshots(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        payload = self._fetch_optional_list(
            source="historical_market_snapshots",
            url=self._interpolate_url(self.snapshots_endpoint_url, market_id),
            fallback_query={"market_id": market_id},
        )
        return payload

    def fetch_orderbook_snapshots(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        if not self.include_orderbook:
            return ()
        payload = self._fetch_optional_list(
            source="historical_orderbook_snapshots",
            url=self._interpolate_url(self.orderbook_endpoint_url, market_id),
            fallback_query={"market_id": market_id},
        )
        return payload

    def fetch_trades(self, *, market_id: str) -> tuple[Mapping[str, Any], ...]:
        if not self.include_trades:
            return ()
        payload = self._fetch_optional_list(
            source="historical_trades",
            url=self._interpolate_url(self.trades_endpoint_url, market_id),
            fallback_query={"market_id": market_id},
        )
        return payload

    def fetch_resolution(self, *, market_id: str, market_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = self._fetch_optional(
            source="historical_resolution",
            url=self._interpolate_url(self.resolutions_endpoint_url, market_id),
            query_params={"market_id": market_id} if self.resolutions_endpoint_url and "{market_id}" not in self.resolutions_endpoint_url else None,
        )
        if isinstance(payload, Mapping):
            return payload
        return {
            "market_id": market_id,
            "resolved_outcome": (
                market_payload.get("resolved_outcome")
                or market_payload.get("resolution")
                or market_payload.get("outcome")
                or ""
            ),
            "resolved_at": market_payload.get("resolved_at") or market_payload.get("resolutionDate"),
            "status": market_payload.get("status") or "",
        }

    def _fetch_optional_list(
        self,
        *,
        source: str,
        url: str,
        fallback_query: Mapping[str, str],
    ) -> tuple[Mapping[str, Any], ...]:
        payload = self._fetch_optional(
            source=source,
            url=url,
            query_params=fallback_query if "{market_id}" not in url else None,
        )
        if isinstance(payload, list):
            return tuple(row for row in payload if isinstance(row, Mapping))
        if isinstance(payload, Mapping):
            rows = payload.get("rows")
            if isinstance(rows, list):
                return tuple(row for row in rows if isinstance(row, Mapping))
            data = payload.get("data")
            if isinstance(data, list):
                return tuple(row for row in data if isinstance(row, Mapping))
        return ()

    def _fetch_optional(
        self,
        *,
        source: str,
        url: str,
        query_params: Mapping[str, str] | None = None,
    ) -> Any:
        if not url.strip():
            return None
        try:
            response = self._fetch(source=source, url=url, query_params=query_params)
            return response.payload
        except HttpClientError:
            return None

    def _fetch(
        self,
        *,
        source: str,
        url: str,
        query_params: Mapping[str, str] | None = None,
    ) -> Any:
        if self.throttle_sec > 0:
            time.sleep(self.throttle_sec)
        return self.http_client.fetch_json(
            source=source,
            url=url,
            query_params=query_params,
            use_cache=False,
        )

    @staticmethod
    def _interpolate_url(template: str, value: str) -> str:
        if not template.strip():
            return ""
        if "{market_id}" in template:
            return template.replace("{market_id}", value)
        if "{event_id}" in template:
            return template.replace("{event_id}", value)
        return template

