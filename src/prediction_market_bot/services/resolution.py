from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.domain.enums import ResolutionStatus
from prediction_market_bot.domain.models import PendingSettlementRequest, ResolutionCheckResult
from prediction_market_bot.interfaces import ResolutionPollerPort


class DeterministicResolutionPoller(ResolutionPollerPort):
    """
    Deterministic resolver used in dry-run mode.

    In production-like integrations, replace this with a venue adapter poller.
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

        checksum = sum(ord(char) for char in request.market_id)
        pivot = int(round(request.fill_price * 1000))
        resolved_yes = ((checksum + pivot) % 2) == 0
        return ResolutionCheckResult(
            market_id=request.market_id,
            status=ResolutionStatus.RESOLVED,
            resolved_yes=resolved_yes,
            reason="resolution_resolved_deterministic",
            checked_at=datetime.now(UTC),
        )
