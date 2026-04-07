from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from prediction_market_bot.domain.models import MarketCandidate, ResearchPacket

RUNTIME_PREDICTION_FEATURE_SCHEMA_VERSION = "v1"
ALT_SHADOW_RUNTIME_FEATURE_SCHEMA_VERSION = "alt-v1"


def _clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _as_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def _feature_from_bundle(bundle: dict[str, float], key: str, *, default: float = 0.0) -> float:
    raw = bundle.get(key)
    if raw is None:
        return default
    value = _as_float(raw, default=default)
    if not math.isfinite(value):
        return default
    return value


def _feature_from_bundle_aliases(
    bundle: dict[str, float],
    keys: Sequence[str],
    *,
    default: float = 0.0,
) -> float:
    for key in keys:
        value = _feature_from_bundle(bundle, key, default=default)
        if value != default:
            return value
    return default


def _category_hash_bucket(category: str) -> int:
    if not category:
        return 0
    digest = 0
    for byte in category.encode("utf-8"):
        digest = ((digest * 31) + int(byte)) % 2_147_483_647
    return digest % 128


@dataclass(slots=True, frozen=True)
class RuntimePredictionFeatures:
    schema_version: str
    decision_timestamp_utc: datetime
    values: dict[str, float]


def build_runtime_prediction_features(candidate: MarketCandidate, research: ResearchPacket) -> RuntimePredictionFeatures:
    market = candidate.market
    as_of = market.updated_at.astimezone(UTC) if market.updated_at.tzinfo is not None else market.updated_at.replace(tzinfo=UTC)
    yes_price = _clamp(float(market.yes_price), lower=1e-6, upper=1.0 - 1e-6)
    no_price = _clamp(float(market.no_price), lower=1e-6, upper=1.0 - 1e-6)
    price_logit = math.log(yes_price / (1.0 - yes_price))
    price_move = float(market.last_price_move_bps) / 10_000.0
    spread_bps = max(float(market.spread_bps), 0.0)
    liquidity_usd = max(float(market.liquidity_usd), 0.0)
    volume_24h_usd = max(float(market.volume_24h_usd), 0.0)
    hours_to_close = max(float(market.hours_to_resolution), 0.0)
    category = market.category.strip().lower()

    bundle = dict(research.feature_bundle or {})
    findings_count_default = float(len(research.findings))
    avg_credibility_default = (
        sum(float(finding.credibility) for finding in research.findings) / len(research.findings)
        if research.findings
        else 0.0
    )
    contradiction_default = _clamp(float(research.disagreement_score), lower=0.0, upper=1.0)
    values = {
        "f_market_yes_price": yes_price,
        "f_market_no_price": no_price,
        "f_market_price_logit": price_logit,
        "f_market_price_distance_0_5": abs(yes_price - 0.5),
        "f_spread_bps": spread_bps,
        "f_liquidity_usd": liquidity_usd,
        "f_bid_ask_imbalance": 0.0,
        "f_volume_24h_usd": volume_24h_usd,
        "f_trades_count_24h": 0.0,
        "f_trade_size_sum_24h": 0.0,
        "f_trade_signed_flow_24h": 0.0,
        "f_hours_to_close": hours_to_close,
        "f_decision_weekday": float(as_of.weekday()),
        "f_decision_hour_utc": float(as_of.hour),
        "f_price_move_1h": price_move,
        "f_price_move_24h": price_move,
        "f_momentum_24h": price_move,
        "f_realized_volatility_24h": abs(price_move),
        "f_category_hash_bucket": float(_category_hash_bucket(category)),
        "f_event_market_count": 1.0,
        "f_event_liquidity_share": 1.0,
        "f_research_findings_count": _feature_from_bundle(
            bundle,
            "research_findings_count",
            default=findings_count_default,
        ),
        "f_research_weighted_sentiment": _feature_from_bundle(
            bundle,
            "weighted_sentiment",
            default=float(research.weighted_sentiment),
        ),
        "f_research_evidence_strength": _feature_from_bundle(
            bundle,
            "evidence_strength",
            default=float(research.evidence_strength),
        ),
        "f_research_market_relevance": _feature_from_bundle(bundle, "market_relevance_score", default=0.0),
        "f_research_timeliness_decay": _feature_from_bundle(bundle, "timeliness_decay", default=0.0),
        "f_research_source_credibility_prior": _feature_from_bundle(bundle, "source_credibility_prior", default=0.0),
        "f_research_avg_credibility": _feature_from_bundle(bundle, "avg_credibility", default=avg_credibility_default),
        "f_research_effective_credibility": _feature_from_bundle(
            bundle,
            "effective_credibility",
            default=avg_credibility_default,
        ),
        "f_research_disagreement": _feature_from_bundle(
            bundle,
            "disagreement_score",
            default=float(research.disagreement_score),
        ),
        "f_research_source_diversity": _feature_from_bundle(bundle, "source_diversity", default=0.0),
        "f_research_contradiction_rate": _feature_from_bundle(bundle, "contradiction_score", default=contradiction_default),
        "f_research_conflict_score": _feature_from_bundle(bundle, "contradiction_score", default=contradiction_default),
        "f_research_freshness_hours": _feature_from_bundle(bundle, "freshness_hours", default=0.0),
        "f_research_entity_event_alignment": _feature_from_bundle(bundle, "entity_event_alignment_score", default=0.0),
        "f_research_evidence_novelty": _feature_from_bundle(bundle, "evidence_novelty_score", default=0.0),
    }
    return RuntimePredictionFeatures(
        schema_version=RUNTIME_PREDICTION_FEATURE_SCHEMA_VERSION,
        decision_timestamp_utc=as_of,
        values=values,
    )


def enrich_with_btc_features(
    base: RuntimePredictionFeatures,
    btc_features: dict[str, float],
) -> RuntimePredictionFeatures:
    """Overlay BTC technical features onto base features and switch schema to btc-v1.

    Used when the prediction target is a BTC Up/Down market:
      - Merges Binance-derived features into the values dict.
      - Sets schema_version to "btc-v1" so the BTC model artifact loads correctly.
    """
    if not btc_features:
        return base
    merged = dict(base.values)
    merged.update(btc_features)
    return RuntimePredictionFeatures(
        schema_version="btc-v1",
        decision_timestamp_utc=base.decision_timestamp_utc,
        values=merged,
    )


def build_alt_shadow_prediction_features(
    candidate: MarketCandidate,
    research: ResearchPacket,
    *,
    base_features: RuntimePredictionFeatures | None = None,
    schema_version: str = ALT_SHADOW_RUNTIME_FEATURE_SCHEMA_VERSION,
) -> RuntimePredictionFeatures:
    base = base_features if base_features is not None else build_runtime_prediction_features(candidate, research)
    bundle = dict(research.feature_bundle or {})
    values = dict(base.values)
    values.update(
        {
            "f_alt_evidence_count": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_evidence_count", "alt_evidence_count"),
                default=0.0,
            ),
            "f_alt_linked_evidence_count": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_linked_evidence_count", "alt_linked_evidence_count"),
                default=0.0,
            ),
            "f_alt_market_linked_coverage": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_market_linked_coverage", "alt_market_linked_coverage"),
                default=0.0,
            ),
            "f_alt_source_diversity": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_source_diversity", "alt_source_diversity"),
                default=0.0,
            ),
            "f_alt_source_credibility_prior": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_source_credibility_prior", "alt_source_credibility_prior"),
                default=0.0,
            ),
            "f_alt_freshness_decay": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_freshness_decay", "alt_freshness_decay"),
                default=0.0,
            ),
            "f_alt_news_volume_24h": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_news_volume_24h", "alt_news_volume_24h"),
                default=0.0,
            ),
            "f_alt_news_burstiness_24h": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_news_burstiness_24h", "alt_news_burstiness_24h"),
                default=0.0,
            ),
            "f_alt_reddit_mentions_24h": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_reddit_mentions_24h", "alt_reddit_mentions_24h"),
                default=0.0,
            ),
            "f_alt_reddit_attention": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_reddit_attention", "alt_reddit_attention"),
                default=0.0,
            ),
            "f_alt_x_mentions_24h": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_x_mentions_24h", "alt_x_mentions_24h"),
                default=0.0,
            ),
            "f_alt_x_attention": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_x_attention", "alt_x_attention"),
                default=0.0,
            ),
            "f_alt_contradiction_score": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_contradiction_score", "alt_contradiction_score"),
                default=0.0,
            ),
            "f_alt_novelty_score": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_novelty_score", "alt_novelty_score"),
                default=0.0,
            ),
            "f_alt_catalyst_strength_score": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_catalyst_strength_score", "alt_catalyst_strength_score"),
                default=0.0,
            ),
            "f_alt_enrichment_coverage": _feature_from_bundle_aliases(
                bundle,
                ("f_alt_enrichment_coverage", "alt_enrichment_coverage"),
                default=0.0,
            ),
        }
    )
    resolved_schema = schema_version.strip() or ALT_SHADOW_RUNTIME_FEATURE_SCHEMA_VERSION
    return RuntimePredictionFeatures(
        schema_version=resolved_schema,
        decision_timestamp_utc=base.decision_timestamp_utc,
        values=values,
    )


def build_alt_shadow_parity_warnings(
    feature_values: Mapping[str, float],
    *,
    required_sources: Sequence[str],
    require_llm_enrichment: bool,
) -> tuple[str, ...]:
    warnings: list[str] = []
    source_feature_map = {
        "news": "f_alt_news_volume_24h",
        "reddit": "f_alt_reddit_mentions_24h",
        "x": "f_alt_x_mentions_24h",
        "llm_enrichment": "f_alt_enrichment_coverage",
    }
    for source in required_sources:
        key = source.strip().lower()
        if not key:
            continue
        feature_key = source_feature_map.get(key)
        if feature_key is None:
            warnings.append(f"missing_source_coverage source={key} reason=unsupported_source_key")
            continue
        if _as_float(feature_values.get(feature_key), default=0.0) <= 0.0:
            warnings.append(f"missing_source_coverage source={key}")
    enrichment_coverage = _as_float(feature_values.get("f_alt_enrichment_coverage"), default=0.0)
    if require_llm_enrichment and enrichment_coverage <= 0.0:
        warnings.append("enrichment_pipeline_failure reason=missing_llm_enrichment_coverage")
    evidence_count = _as_float(feature_values.get("f_alt_evidence_count"), default=0.0)
    if evidence_count > 0.0 and enrichment_coverage <= 0.0:
        warnings.append("enrichment_pipeline_failure reason=no_enrichment_for_available_alt_evidence")
    return tuple(dict.fromkeys(warnings))
