from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from prediction_market_bot.domain.enums import ResolutionStatus
from prediction_market_bot.domain.models import PendingSettlementRequest, ResolutionCheckResult
from prediction_market_bot.interfaces import ResolutionPollerPort

logger = logging.getLogger(__name__)


class PolymarketResolutionPoller(ResolutionPollerPort):
    """Polls the Polymarket Gamma API to check real market resolution.

    Replaces the ``DeterministicResolutionPoller`` for PAPER_LIVE and
    SANDBOX_CHAIN modes.  Markets that are still open return PENDING;
    markets resolved by UMA return RESOLVED with the actual boolean result.

    Endpoint: ``GET https://gamma-api.polymarket.com/markets/{condition_id}``

    The ``condition_id`` is the Polymarket market identifier stored in
    ``PendingSettlementRequest.market_id``.
    """

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        timeout_sec: float = 8.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = max(timeout_sec, 1.0)

    def poll(self, request: PendingSettlementRequest) -> ResolutionCheckResult:
        market_id = (request.market_id or "").strip()
        if not market_id:
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.PENDING,
                resolved_yes=None,
                reason="resolution_pending_missing_market_id",
                checked_at=datetime.now(UTC),
            )

        url = f"{self._base_url}/markets/{market_id}"
        try:
            payload = self._fetch_json(url)
        except Exception as exc:
            logger.warning(
                "polymarket_resolution_poll_failed",
                extra={
                    "event": "polymarket_resolution_poll_failed",
                    "market_id": market_id,
                    "url": url,
                    "error": str(exc),
                },
            )
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.PENDING,
                resolved_yes=None,
                reason=f"resolution_poll_http_error: {exc}",
                checked_at=datetime.now(UTC),
            )

        return self._parse_resolution(market_id, payload)

    def _parse_resolution(
        self,
        market_id: str,
        payload: Any,
    ) -> ResolutionCheckResult:
        now = datetime.now(UTC)
        # Polymarket API can return a list (search results) or a single object.
        if isinstance(payload, list):
            # pick first entry that matches market_id
            market_data: dict[str, Any] | None = None
            for item in payload:
                if isinstance(item, dict):
                    cid = str(item.get("conditionId") or item.get("id") or "").lower()
                    if cid == market_id.lower() or not market_id:
                        market_data = item
                        break
            if market_data is None and payload:
                market_data = payload[0] if isinstance(payload[0], dict) else None
        elif isinstance(payload, dict):
            market_data = payload
        else:
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.PENDING,
                resolved_yes=None,
                reason="resolution_poll_unexpected_response_format",
                checked_at=now,
            )

        if market_data is None:
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.PENDING,
                resolved_yes=None,
                reason="resolution_pending_market_not_found_in_response",
                checked_at=now,
            )

        # Check resolution fields in order of reliability.
        # 1. ``resolution`` field ("YES", "NO", "N/A") — present after UMA settlement
        resolution_str = str(market_data.get("resolution") or "").strip().upper()
        if resolution_str in {"YES", "NO"}:
            resolved_yes = resolution_str == "YES"
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.RESOLVED,
                resolved_yes=resolved_yes,
                reason=f"resolution_resolved_via_resolution_field resolution={resolution_str}",
                checked_at=now,
            )

        # 2. ``is_resolved`` bool + ``outcomePrices`` array [yes_price, no_price]
        is_resolved = bool(market_data.get("isResolved") or market_data.get("is_resolved"))
        if is_resolved:
            outcome_prices = market_data.get("outcomePrices") or []
            if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                try:
                    yes_price = float(outcome_prices[0])
                    # YES resolves to 1.0, NO resolves to 0.0
                    resolved_yes = yes_price > 0.5
                    return ResolutionCheckResult(
                        market_id=market_id,
                        status=ResolutionStatus.RESOLVED,
                        resolved_yes=resolved_yes,
                        reason=(
                            f"resolution_resolved_via_outcomePrices "
                            f"yes_price={yes_price:.4f}"
                        ),
                        checked_at=now,
                    )
                except (ValueError, TypeError):
                    pass
            # resolved but can't parse outcome — treat as ambiguous
            return ResolutionCheckResult(
                market_id=market_id,
                status=ResolutionStatus.AMBIGUOUS,
                resolved_yes=None,
                reason="resolution_ambiguous_is_resolved_but_outcome_unreadable",
                checked_at=now,
            )

        # 3. Active / not yet resolved
        return ResolutionCheckResult(
            market_id=market_id,
            status=ResolutionStatus.PENDING,
            resolved_yes=None,
            reason="resolution_pending_market_not_yet_resolved",
            checked_at=now,
        )

    def _fetch_json(self, url: str) -> Any:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "prediction-market-bot/0.1"},
        )
        with urllib.request.urlopen(req, timeout=self._timeout_sec) as resp:  # nosec B310
            raw = resp.read(1_048_576)
        return json.loads(raw)


class DeterministicResolutionPoller(ResolutionPollerPort):
    """
    Probability-weighted deterministic resolver for paper/dry-run mode.

    Replaces the flat XOR-hash with a resolution that respects market prices:
    a market trading at 70% YES resolves YES ~70% of the time.

    The resolution is still fully deterministic (seeded from market_id and
    fill_price) so replays produce identical results.

    In a live integration replace this with a venue adapter that polls
    the Polymarket settlement API.
    """

    def poll(self, request: PendingSettlementRequest) -> ResolutionCheckResult:
        if request.fill_price is None:
            return ResolutionCheckResult(
                market_id=request.market_id,
                status=ResolutionStatus.PENDING,
                resolved_yes=None,
                reason="resolution_pending_missing_fill_price",
                checked_at=datetime.now(UTC),
            )

        # Seed a deterministic pseudo-random draw from market_id + fill_price.
        # We use a simple hash-based LCG to stay dependency-free.
        seed = sum(ord(c) * (i + 1) for i, c in enumerate(request.market_id))
        seed ^= int(round(request.fill_price * 1_000_000))
        # LCG: a=1664525, c=1013904223, m=2^32 (Numerical Recipes)
        lcg = ((seed * 1_664_525 + 1_013_904_223) & 0xFFFF_FFFF)
        lcg = ((lcg * 1_664_525 + 1_013_904_223) & 0xFFFF_FFFF)
        draw = lcg / 0x1_0000_0000  # uniform [0, 1)

        # YES resolves with probability equal to the YES fill_price,
        # so a market at 0.70 YES resolves YES ~70% of the time.
        yes_prob = max(0.01, min(0.99, float(request.fill_price)))
        resolved_yes = draw < yes_prob

        return ResolutionCheckResult(
            market_id=request.market_id,
            status=ResolutionStatus.RESOLVED,
            resolved_yes=resolved_yes,
            reason=f"resolution_resolved_probability_weighted yes_prob={yes_prob:.4f} draw={draw:.4f}",
            checked_at=datetime.now(UTC),
        )

