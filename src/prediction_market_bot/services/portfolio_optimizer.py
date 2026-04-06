"""Portfolio optimization algorithms for capital allocation.

Provides three pure-Python allocation strategies that operate on a mapping of
market_id → raw Kelly fraction (positive edge only):

* ``kelly_multi_asset``  — scale Kelly fractions so the total portfolio
  allocation stays within a configurable cap.
* ``risk_parity``        — equal-weight allocation (each approved market gets
  the same fraction), capped per position.
* ``min_variance``       — greedy minimum-correlation selection; reduces
  co-movement by preferring markets whose categories differ from those already
  in the portfolio.

All methods return an :class:`AllocationResult` containing the final
``allocations`` dict (market_id → fraction_of_bankroll), the method name,
and the total allocated fraction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(slots=True, frozen=True)
class AllocationResult:
    """Output of a portfolio optimization run."""

    allocations: dict[str, float]
    method: str
    total_allocated_fraction: float
    capped: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "total_allocated_fraction": self.total_allocated_fraction,
            "capped": self.capped,
            "notes": list(self.notes),
            "allocations": dict(self.allocations),
        }


@dataclass(slots=True, frozen=True)
class PortfolioOptimizer:
    """Stateless allocator — all state is passed in per call."""

    max_portfolio_fraction: float = 0.10
    max_position_fraction: float = 0.02
    min_position_fraction: float = 0.001

    # ── Public API ────────────────────────────────────────────────────────────

    def kelly_multi_asset(
        self,
        raw_kelly_fractions: Mapping[str, float],
    ) -> AllocationResult:
        """Scale Kelly fractions proportionally so the total does not exceed
        ``max_portfolio_fraction``, then clip each position to
        ``max_position_fraction``.

        Args:
            raw_kelly_fractions: market_id → raw Kelly fraction (positive only;
                non-positive values are filtered out).
        """
        candidates = {k: v for k, v in raw_kelly_fractions.items() if v > 0}
        if not candidates:
            return self._empty("kelly_multi_asset")

        total_raw = sum(candidates.values())
        notes: list[str] = []
        capped = False

        if total_raw > self.max_portfolio_fraction:
            scale = self.max_portfolio_fraction / total_raw
            scaled = {k: v * scale for k, v in candidates.items()}
            notes.append(f"scaled_down_by={scale:.4f} total_raw={total_raw:.4f}")
            capped = True
        else:
            scaled = dict(candidates)

        # Per-position cap
        allocations = {
            k: min(v, self.max_position_fraction)
            for k, v in scaled.items()
            if v >= self.min_position_fraction
        }
        if len(allocations) < len(scaled):
            notes.append(f"dropped_{len(scaled) - len(allocations)}_below_min_fraction")

        total = sum(allocations.values())
        return AllocationResult(
            allocations=allocations,
            method="kelly_multi_asset",
            total_allocated_fraction=round(total, 8),
            capped=capped,
            notes=tuple(notes),
        )

    def risk_parity(
        self,
        raw_kelly_fractions: Mapping[str, float],
    ) -> AllocationResult:
        """Equal-weight allocation across all markets with positive Kelly.

        Each approved market receives the same fraction:
        ``min(max_position_fraction, max_portfolio_fraction / n_markets)``.
        This minimises concentration risk when edge estimates are uncertain.
        """
        candidates = [k for k, v in raw_kelly_fractions.items() if v > 0]
        if not candidates:
            return self._empty("risk_parity")

        n = len(candidates)
        equal_fraction = min(
            self.max_position_fraction,
            self.max_portfolio_fraction / n,
        )
        allocations = {k: equal_fraction for k in candidates}
        total = equal_fraction * n
        capped = total > self.max_portfolio_fraction - 1e-9 or equal_fraction >= self.max_position_fraction
        return AllocationResult(
            allocations=allocations,
            method="risk_parity",
            total_allocated_fraction=round(total, 8),
            capped=capped,
            notes=(f"equal_fraction={equal_fraction:.6f}", f"n_markets={n}"),
        )

    def min_variance(
        self,
        raw_kelly_fractions: Mapping[str, float],
        market_categories: Mapping[str, str] | None = None,
    ) -> AllocationResult:
        """Greedy category-diversified allocation (no numpy required).

        Selects markets in descending Kelly order, skipping markets whose
        category is already at ``max_per_category`` positions.  Stops when the
        total allocation reaches ``max_portfolio_fraction``.

        Args:
            raw_kelly_fractions: market_id → raw Kelly fraction.
            market_categories: Optional market_id → category string used to
                limit concentration.  When omitted, all markets are treated as
                distinct categories.
        """
        candidates = sorted(
            [(k, v) for k, v in raw_kelly_fractions.items() if v > 0],
            key=lambda kv: kv[1],
            reverse=True,
        )
        if not candidates:
            return self._empty("min_variance")

        cats = market_categories or {}
        max_per_category = max(1, len(candidates) // 3)  # heuristic
        category_counts: dict[str, int] = {}
        allocations: dict[str, float] = {}
        total = 0.0
        notes: list[str] = []

        for market_id, kelly in candidates:
            if total >= self.max_portfolio_fraction - 1e-9:
                notes.append(f"stopped_at_{len(allocations)}_markets_portfolio_cap_reached")
                break
            cat = cats.get(market_id, market_id)  # unique key if no category
            if category_counts.get(cat, 0) >= max_per_category:
                continue
            fraction = min(kelly, self.max_position_fraction, self.max_portfolio_fraction - total)
            if fraction < self.min_position_fraction:
                continue
            allocations[market_id] = fraction
            total += fraction
            category_counts[cat] = category_counts.get(cat, 0) + 1

        capped = total >= self.max_portfolio_fraction - 1e-9
        return AllocationResult(
            allocations=allocations,
            method="min_variance",
            total_allocated_fraction=round(total, 8),
            capped=capped,
            notes=tuple(notes) or (f"selected_{len(allocations)}_markets",),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _empty(self, method: str) -> AllocationResult:
        return AllocationResult(
            allocations={},
            method=method,
            total_allocated_fraction=0.0,
            capped=False,
            notes=("no_candidates_with_positive_edge",),
        )
