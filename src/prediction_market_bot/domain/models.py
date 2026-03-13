from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    ExecutionStatus,
    MarketStatus,
    OutcomeClassification,
    OutcomeSide,
    PostmortemCause,
    SourceType,
    TradeReviewAction,
    TradeReviewStatus,
)

Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveAmount = Annotated[float, Field(ge=0.0)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Market(StrictModel):
    market_id: NonEmptyStr
    venue: NonEmptyStr
    title: NonEmptyStr
    category: NonEmptyStr = "general"
    status: MarketStatus = MarketStatus.OPEN
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OutcomeQuote(StrictModel):
    side: OutcomeSide
    price: Probability
    liquidity_usd: PositiveAmount = 0.0


class MarketSnapshot(StrictModel):
    market: Market
    outcome_quotes: tuple[OutcomeQuote, ...]
    liquidity_usd: PositiveAmount
    volume_24h_usd: PositiveAmount
    spread_bps: Annotated[int, Field(ge=0)]
    hours_to_resolution: PositiveAmount
    last_price_move_bps: int

    @classmethod
    def from_yes_price(
        cls,
        *,
        market_id: str,
        venue: str,
        title: str,
        yes_price: float,
        liquidity_usd: float,
        volume_24h_usd: float,
        spread_bps: int,
        hours_to_resolution: float,
        last_price_move_bps: int,
        category: str = "general",
        status: MarketStatus = MarketStatus.OPEN,
        updated_at: datetime | None = None,
    ) -> "MarketSnapshot":
        market = Market(
            market_id=market_id,
            venue=venue,
            title=title,
            category=category,
            status=status,
            updated_at=updated_at or datetime.now(UTC),
        )
        return cls(
            market=market,
            outcome_quotes=(
                OutcomeQuote(side=OutcomeSide.YES, price=yes_price, liquidity_usd=liquidity_usd),
                OutcomeQuote(side=OutcomeSide.NO, price=1.0 - yes_price, liquidity_usd=liquidity_usd),
            ),
            liquidity_usd=liquidity_usd,
            volume_24h_usd=volume_24h_usd,
            spread_bps=spread_bps,
            hours_to_resolution=hours_to_resolution,
            last_price_move_bps=last_price_move_bps,
        )

    @model_validator(mode="after")
    def _validate_quotes(self) -> "MarketSnapshot":
        if len(self.outcome_quotes) < 2:
            raise ValueError("MarketSnapshot must include at least YES and NO quotes.")
        by_side = {quote.side: quote for quote in self.outcome_quotes}
        if OutcomeSide.YES not in by_side or OutcomeSide.NO not in by_side:
            raise ValueError("MarketSnapshot must include both YES and NO outcome quotes.")
        yes_price = by_side[OutcomeSide.YES].price
        no_price = by_side[OutcomeSide.NO].price
        if abs((yes_price + no_price) - 1.0) > 1e-6:
            raise ValueError("YES and NO prices must sum to 1.0.")
        return self

    @property
    def market_id(self) -> str:
        return self.market.market_id

    @property
    def venue(self) -> str:
        return self.market.venue

    @property
    def title(self) -> str:
        return self.market.title

    @property
    def category(self) -> str:
        return self.market.category

    @property
    def status(self) -> MarketStatus:
        return self.market.status

    @property
    def updated_at(self) -> datetime:
        return self.market.updated_at

    @property
    def yes_price(self) -> float:
        return self._quote(OutcomeSide.YES).price

    @property
    def no_price(self) -> float:
        return self._quote(OutcomeSide.NO).price

    def _quote(self, side: OutcomeSide) -> OutcomeQuote:
        for quote in self.outcome_quotes:
            if quote.side == side:
                return quote
        raise ValueError(f"Missing quote for side {side.value}")


class MarketCandidate(StrictModel):
    market: MarketSnapshot
    scan_score: Annotated[float, Field(ge=0.0, le=1.0)]
    reasons: tuple[str, ...] = ()


class ResearchFinding(StrictModel):
    source_type: SourceType
    source_name: NonEmptyStr
    summary: NonEmptyStr
    sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    credibility: Probability
    url: str = ""
    provenance: tuple[NonEmptyStr, ...] = ()


class ResearchPacket(StrictModel):
    market_id: NonEmptyStr
    findings: tuple[ResearchFinding, ...]
    weighted_sentiment: Annotated[float, Field(ge=-1.0, le=1.0)]
    evidence_strength: Probability
    disagreement_score: Probability
    narrative_summary: str


class PredictionResult(StrictModel):
    market_id: NonEmptyStr
    selected_side: OutcomeSide
    market_yes_prob: Probability
    fair_yes_prob: Probability
    selected_market_price: Probability
    selected_fair_price: Probability
    edge: float
    confidence: Probability
    rationale: tuple[str, ...] = ()


class RiskDecision(StrictModel):
    market_id: NonEmptyStr
    approved: bool
    side: OutcomeSide
    stake_usd: PositiveAmount
    bankroll_fraction: Probability
    fractional_kelly: PositiveAmount
    max_loss_usd: PositiveAmount
    reasoning: tuple[str, ...] = ()


class OrderIntent(StrictModel):
    market_id: NonEmptyStr
    venue: NonEmptyStr
    side: OutcomeSide
    stake_usd: PositiveAmount
    limit_price: Probability
    rationale: str


class ExecutionResult(StrictModel):
    market_id: NonEmptyStr
    status: ExecutionStatus
    side: OutcomeSide
    stake_usd: PositiveAmount
    fill_price: Probability | None = None
    order_id: str | None = None
    message: str = ""


class SettlementResult(StrictModel):
    market_id: NonEmptyStr
    side: OutcomeSide
    stake_usd: PositiveAmount
    avg_price: Probability
    resolved_yes: bool
    pnl_usd: float
    outcome_classification: OutcomeClassification = OutcomeClassification.BREAKEVEN


class PostmortemReport(StrictModel):
    market_id: NonEmptyStr
    outcome_classification: OutcomeClassification = OutcomeClassification.BREAKEVEN
    causes: tuple[PostmortemCause, ...]
    summary: str
    action_items: tuple[str, ...]


class TradeReviewCandidate(StrictModel):
    queue_id: NonEmptyStr
    run_id: NonEmptyStr
    market_id: NonEmptyStr
    side: OutcomeSide
    stake_usd: PositiveAmount
    confidence: Probability
    edge: float
    prediction_rationale: tuple[str, ...] = ()
    risk_rationale: tuple[str, ...] = ()
    model_rationale: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TradeReviewDecision(StrictModel):
    queue_id: NonEmptyStr
    run_id: NonEmptyStr
    market_id: NonEmptyStr
    action: TradeReviewAction
    status_after_action: TradeReviewStatus
    operator_id: NonEmptyStr
    operator_rationale: NonEmptyStr
    note: str = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TradeReviewItem(StrictModel):
    queue_id: NonEmptyStr
    run_id: NonEmptyStr
    market_id: NonEmptyStr
    side: OutcomeSide
    stake_usd: PositiveAmount
    confidence: Probability
    edge: float
    status: TradeReviewStatus = TradeReviewStatus.PENDING
    prediction_rationale: tuple[str, ...] = ()
    risk_rationale: tuple[str, ...] = ()
    model_rationale: tuple[str, ...] = ()
    operator_rationale: str = ""
    notes: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Backward compatibility for earlier module names.
SettledTrade = SettlementResult
