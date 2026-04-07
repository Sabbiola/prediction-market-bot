from __future__ import annotations

from typing import Sequence

from prediction_market_bot.app.settings import ScanSettings
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot


class ScanAgent:
    name = "scan-agent"

    def __init__(self, settings: ScanSettings) -> None:
        self.settings = settings

    def run(self, markets: Sequence[MarketSnapshot]) -> list[MarketCandidate]:
        candidates: list[MarketCandidate] = []

        for market in markets:
            if not self._is_eligible(market):
                continue

            score, reasons = self._score_market(market)
            candidates.append(MarketCandidate(market=market, scan_score=score, reasons=reasons))

        return sorted(candidates, key=self._ranking_key)

    def _is_eligible(self, market: MarketSnapshot) -> bool:
        if market.status.value != "OPEN":
            return False
        if market.liquidity_usd < self.settings.min_liquidity_usd:
            return False
        if market.volume_24h_usd < self.settings.min_volume_24h_usd:
            return False
        if market.hours_to_resolution < self.settings.min_hours_to_resolution:
            return False
        if market.spread_bps > self.settings.max_spread_bps:
            return False
        return True

    def _score_market(self, market: MarketSnapshot) -> tuple[float, tuple[str, ...]]:
        liquidity_score = min(market.liquidity_usd / (self.settings.min_liquidity_usd * 5.0), 1.0)
        spread_score = 1.0 - min(market.spread_bps / max(self.settings.max_spread_bps, 1), 1.0)
        activity_score = min(market.volume_24h_usd / max(self.settings.min_volume_24h_usd * 5.0, 1.0), 1.0)
        time_score = min(self.settings.min_hours_to_resolution / max(market.hours_to_resolution, 1.0), 1.0)

        score = round(
            (liquidity_score * 0.35)
            + (spread_score * 0.25)
            + (activity_score * 0.25)
            + (time_score * 0.15),
            4,
        )

        reasons: list[str] = []
        if liquidity_score >= 0.7:
            reasons.append("high_liquidity")
        if spread_score >= 0.7:
            reasons.append("tight_spread")
        if activity_score >= 0.7:
            reasons.append("high_activity")
        if time_score >= 0.7:
            reasons.append("near_resolution")

        return score, tuple(reasons)

    @staticmethod
    def _ranking_key(candidate: MarketCandidate) -> tuple[float, float, int, float, float, str]:
        market = candidate.market
        return (
            -candidate.scan_score,
            -market.liquidity_usd,
            market.spread_bps,
            -market.volume_24h_usd,
            market.hours_to_resolution,
            market.market_id,
        )
