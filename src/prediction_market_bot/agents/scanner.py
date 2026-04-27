from __future__ import annotations

from typing import Sequence

from prediction_market_bot.agents.btc_feature_enricher import is_btc_updown_market
from prediction_market_bot.app.settings import ScanSettings
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot


class ScanAgent:
    name = "scan-agent"

    def __init__(self, settings: ScanSettings) -> None:
        self.settings = settings

    def run(self, markets: Sequence[MarketSnapshot]) -> list[MarketCandidate]:
        candidates: list[MarketCandidate] = []

        for market in markets:
            if self.settings.btc_only_mode:
                title = market.title or ""
                if not is_btc_updown_market(title, title):
                    continue
            if not self._is_eligible(market):
                continue

            score, reasons = self._score_market(market)
            candidates.append(MarketCandidate(market=market, scan_score=score, reasons=reasons))

        candidates = self._enforce_btc_single_slot(
            candidates,
            btc_only=self.settings.btc_only_mode,
        )
        return sorted(candidates, key=self._ranking_key)

    # BTC 15 m model sweet spot.
    # The catboost model was trained on 15-minute BTC direction. Its predictive
    # edge collapses in the final ~9 min of a slot, when the live BTC price
    # already fully encodes the slot's outcome (the market quote becomes
    # near-perfect, so the model has no room to disagree profitably).
    #
    # Thresholds:
    #   - BTC_MIN_H_TO_RES = 0.15 h (9 min): skip slots closer than this —
    #     model can't beat the market.
    #   - BTC_MAX_H_TO_RES = 0.50 h (30 min): cover up to the next 1-2 slots
    #     ahead; Polymarket BTC Up/Down series spacing is 15 min so, with
    #     scans every 5 min, there is almost always exactly ONE slot whose
    #     h_to_res sits inside [0.15, 0.50].
    BTC_MIN_H_TO_RES = 0.15
    BTC_MAX_H_TO_RES = 0.50

    @classmethod
    def _enforce_btc_single_slot(
        cls,
        candidates: list[MarketCandidate],
        *,
        btc_only: bool = False,
    ) -> list[MarketCandidate]:
        """For Polymarket BTC Up/Down series: keep at most ONE slot per tick —
        the next-opened slot whose horizon is in the model's sweet spot
        (``[BTC_MIN_H_TO_RES, BTC_MAX_H_TO_RES]`` hours to resolution).

        If no BTC slot falls in that window (e.g. the only in-progress slot is
        in its final minutes), all BTC candidates are dropped — no trade is
        better than a coin-flip trade. Non-BTC candidates are untouched.
        """
        btc_candidates: list[MarketCandidate] = []
        other_candidates: list[MarketCandidate] = []
        for candidate in candidates:
            title = candidate.market.title or ""
            if is_btc_updown_market(title, title):
                btc_candidates.append(candidate)
            else:
                other_candidates.append(candidate)

        if not btc_candidates:
            return [] if btc_only else other_candidates

        eligible = [
            c for c in btc_candidates
            if cls.BTC_MIN_H_TO_RES <= c.market.hours_to_resolution <= cls.BTC_MAX_H_TO_RES
        ]
        if not eligible:
            # No slot in the sweet spot → skip BTC this tick (profitable choice).
            return [] if btc_only else other_candidates

        # Pick the in-progress slot (smallest h_to_res still in the sweet spot).
        # Why: a slot's REFERENCE PRICE (BTC at slot open, e.g. 17:15:00) is
        # only locked once the slot has actually opened. Trading the upcoming
        # slot before its open means betting on a 15-min direction from an
        # unknown anchor — the market itself prices that as ~50/50 with no
        # information. The just-opened slot, by contrast, has a fixed anchor
        # and 10-15 minutes of horizon left, which is exactly what the
        # 15-minute catboost model was trained for.
        # Tie-break by scan score, then market_id for determinism.
        best_btc = min(
            eligible,
            key=lambda c: (
                c.market.hours_to_resolution,
                -c.scan_score,
                c.market.market_id,
            ),
        )
        return [best_btc] if btc_only else [best_btc, *other_candidates]

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
