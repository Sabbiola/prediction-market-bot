from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.domain.enums import ResolutionStatus
from prediction_market_bot.domain.models import PendingSettlementRequest, ResolutionCheckResult
from prediction_market_bot.interfaces import ResolutionPollerPort


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

