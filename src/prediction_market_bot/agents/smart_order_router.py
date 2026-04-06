"""Smart Order Router (SOR) — selects the best venue and price for a trade.

Currently only Polymarket is supported as a live venue.  The architecture is
designed for multi-venue extension: add a new :class:`VenueAdapter` subclass
and register it with :class:`SmartOrderRouter`.

Typical usage::

    router = SmartOrderRouter(venues=[PolymarketVenueAdapter()])
    result = router.route(
        market_id="0xabc...",
        side="YES",
        size_usd=100.0,
    )
    print(result.selected_venue, result.quote.price)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VenueQuote:
    """A single-venue price quote for an order."""

    venue_id: str
    market_id: str
    side: str  # "YES" | "NO"
    price: float  # probability / share price in [0, 1]
    max_size_usd: float  # maximum fillable size at this price
    fee_bps: int  # round-trip fee in basis points
    available: bool = True
    note: str = ""

    def effective_price(self) -> float:
        """Price adjusted for fees (buy-side: price + fee)."""
        return min(self.price + self.fee_bps / 10_000.0, 1.0)


@dataclass(slots=True, frozen=True)
class RoutingResult:
    """The outcome of a routing decision."""

    selected_venue: str
    quote: VenueQuote
    alt_quotes: tuple[VenueQuote, ...]
    reasoning: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "selected_venue": self.selected_venue,
            "price": self.quote.price,
            "effective_price": self.quote.effective_price(),
            "fee_bps": self.quote.fee_bps,
            "max_size_usd": self.quote.max_size_usd,
            "alt_venues": [q.venue_id for q in self.alt_quotes],
            "reasoning": list(self.reasoning),
        }


class VenueAdapter(Protocol):
    """Protocol that venue adapters must implement."""

    @property
    def venue_id(self) -> str:
        ...

    def get_quote(self, market_id: str, side: str, size_usd: float) -> VenueQuote:
        """Return a quote for the given order parameters.

        Must not raise; return a ``VenueQuote(available=False)`` on failure.
        """
        ...


@dataclass(slots=True)
class PolymarketVenueAdapter:
    """Polymarket venue adapter (pass-through — prices come from MarketSnapshot).

    In live mode the actual price is fetched from the Polymarket CLOB API.
    In dry-run / paper mode this adapter uses the price already present in the
    :class:`~prediction_market_bot.domain.models.MarketSnapshot`.
    """

    venue_id: str = "polymarket"
    fee_bps: int = 0  # Polymarket charges no maker fee on conditional tokens
    max_size_usd: float = 50_000.0

    def get_quote(self, market_id: str, side: str, size_usd: float) -> VenueQuote:
        del size_usd  # not used in pass-through mode; kept for API compatibility
        # In the current paper-trading setup the price is resolved by the
        # PredictionResult / MarketSnapshot at decision time, so we return a
        # sentinel quote that delegates price to the caller.
        return VenueQuote(
            venue_id=self.venue_id,
            market_id=market_id,
            side=side,
            price=float("nan"),  # caller supplies the price from MarketSnapshot
            max_size_usd=self.max_size_usd,
            fee_bps=self.fee_bps,
            available=True,
            note="price_from_market_snapshot",
        )


@dataclass(slots=True)
class SmartOrderRouter:
    """Routes an order to the venue offering the best effective price.

    Selection criteria (in priority order):
    1. Venue must be available (``quote.available = True``).
    2. Venue must support the requested size (``quote.max_size_usd >= size_usd``).
    3. Lowest *effective* price (price + fee) wins for buy orders.

    When only one venue is available, it is selected without comparison.
    """

    venues: Sequence[VenueAdapter]

    def route(
        self,
        market_id: str,
        side: str,
        size_usd: float,
    ) -> RoutingResult:
        """Return the best routing decision for the given order.

        Args:
            market_id: Platform-specific market identifier.
            side: ``"YES"`` or ``"NO"``.
            size_usd: Desired order size in USD.

        Returns:
            :class:`RoutingResult` with the selected venue and all collected
            quotes.  If no venue is available, the result contains the first
            quote (unavailable) with an explanatory reasoning entry.
        """
        if not self.venues:
            raise ValueError("SmartOrderRouter has no configured venues")

        quotes: list[VenueQuote] = []
        for venue in self.venues:
            try:
                quote = venue.get_quote(market_id=market_id, side=side, size_usd=size_usd)
            except Exception as exc:
                logger.warning(
                    "sor_venue_quote_failed",
                    extra={"event": "sor_venue_quote_failed", "venue": venue.venue_id, "error": str(exc)},
                )
                quote = VenueQuote(
                    venue_id=venue.venue_id,
                    market_id=market_id,
                    side=side,
                    price=1.0,
                    max_size_usd=0.0,
                    fee_bps=0,
                    available=False,
                    note=f"fetch_error: {type(exc).__name__}",
                )
            quotes.append(quote)

        # Filter: available and can fill the order
        eligible = [q for q in quotes if q.available and q.max_size_usd >= size_usd - 1e-6]
        reasoning: list[str] = [f"venues_queried={len(quotes)}", f"eligible={len(eligible)}"]

        if not eligible:
            reasoning.append("no_eligible_venue_falling_back_to_first")
            best = quotes[0]
        elif len(eligible) == 1:
            best = eligible[0]
            reasoning.append(f"single_eligible_venue={best.venue_id}")
        else:
            # Pick lowest effective price (best fill for buyer)
            best = min(eligible, key=lambda q: q.effective_price())
            reasoning.append(
                f"selected={best.venue_id} effective_price={best.effective_price():.6f} "
                f"vs_alternatives={[f'{q.venue_id}:{q.effective_price():.6f}' for q in eligible if q.venue_id != best.venue_id]}"
            )

        alt_quotes = tuple(q for q in quotes if q.venue_id != best.venue_id)
        return RoutingResult(
            selected_venue=best.venue_id,
            quote=best,
            alt_quotes=alt_quotes,
            reasoning=tuple(reasoning),
        )
