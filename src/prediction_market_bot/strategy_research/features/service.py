from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.services.research_features import (
    ResearchEvidencePoint,
    build_research_feature_bundle,
)

from .models import (
    FeatureBuildSummary,
    FeatureColumn,
    FeatureParityVerification,
    FeatureSchemaInspection,
)
from .schema import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from .storage import FeatureDatasetLayout, FeatureDatasetStorage


def parse_datetime_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        if len(text) == 10 and text.count("-") == 2:
            try:
                parsed = datetime.fromisoformat(f"{text}T00:00:00+00:00")
            except ValueError:
                return None
        else:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp(value: float, *, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


@dataclass(slots=True, frozen=True)
class _EvidencePoint:
    summary: str
    sentiment: float
    credibility: float
    source_identity: str
    source_name: str
    source_type: SourceType
    query: str
    relevance_hint: float | None
    published_at_utc: datetime


class FeatureDatasetBuilderService:
    def __init__(
        self,
        *,
        historical_base_dir: Path,
        research_corpus_base_dir: Path,
        default_dataset_id: str,
        default_corpus_id: str,
        schema_version: str = FEATURE_SCHEMA_VERSION,
    ) -> None:
        self.historical_base_dir = historical_base_dir
        self.research_corpus_base_dir = research_corpus_base_dir
        self.default_dataset_id = default_dataset_id
        self.default_corpus_id = default_corpus_id
        self.schema_version = schema_version

    def build_feature_dataset(
        self,
        *,
        dataset_id: str | None = None,
        corpus_id: str | None = None,
    ) -> FeatureBuildSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        layout = FeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
        )
        storage = FeatureDatasetStorage(layout)

        normalized_root = layout.dataset_root / "normalized"
        markets_path = normalized_root / "markets.jsonl"
        if not markets_path.exists():
            raise FileNotFoundError(f"Historical dataset missing markets file: {markets_path}")
        market_rows = self._read_jsonl(markets_path)
        resolution_rows = self._read_jsonl(normalized_root / "resolutions.jsonl")
        snapshot_rows = self._read_jsonl(normalized_root / "market_snapshots.jsonl")
        orderbook_rows = self._read_jsonl(normalized_root / "orderbook_snapshots.jsonl")
        trade_rows = self._read_jsonl(normalized_root / "trades.jsonl")

        resolution_by_market = self._resolution_by_market(resolution_rows)
        snapshot_series = self._snapshot_series(snapshot_rows)
        orderbook_series = self._orderbook_series(orderbook_rows)
        trade_series = self._trade_series(trade_rows)
        evidence = self._evidence_by_market_and_decision(corpus_id=effective_corpus_id)
        event_market_count, event_total_liquidity = self._event_stats(market_rows)

        warnings: list[str] = []
        rows: list[dict[str, Any]] = []
        seen_row_ids: set[str] = set()
        skipped_missing_decision = 0
        skipped_unresolved = 0
        duplicate_rows = 0

        for market_row in market_rows:
            market_id = str(market_row.get("market_id") or "").strip()
            if not market_id:
                continue
            decision_ts = self._resolve_decision_timestamp(market_row)
            if decision_ts is None:
                skipped_missing_decision += 1
                continue
            outcome = self._resolve_outcome(market_row, resolution_by_market.get(market_id))
            if outcome not in {"YES", "NO"}:
                skipped_unresolved += 1
                continue
            label_yes = 1 if outcome == "YES" else 0
            decision_iso = decision_ts.isoformat()
            row_id = f"{market_id}@{decision_iso}"
            if row_id in seen_row_ids:
                duplicate_rows += 1
                continue
            seen_row_ids.add(row_id)
            flags: set[str] = set()

            snapshots = snapshot_series.get(market_id, ())
            orderbooks = orderbook_series.get(market_id, ())
            trades = trade_series.get(market_id, ())
            research_points = evidence.get((market_id, decision_iso), ())

            yes_price = self._to_float(market_row.get("yes_price_last"))
            if yes_price is None:
                yes_price = self._last_price_at_or_before(snapshots, decision_ts)
                if yes_price is None:
                    yes_price = 0.5
                    flags.add("market_yes_price_fallback_0_5")
                else:
                    flags.add("market_yes_price_from_snapshots")
            yes_price = clamp(yes_price, lower=0.01, upper=0.99)
            no_price = 1.0 - yes_price

            market_logit = math.log(yes_price / (1.0 - yes_price))
            distance_0_5 = abs(yes_price - 0.5)

            best_bid, best_ask, bid_size, ask_size = self._last_orderbook_at_or_before(orderbooks, decision_ts)
            spread_bps = max((best_ask - best_bid) * 10_000.0, 0.0) if best_bid is not None and best_ask is not None else 0.0
            if best_bid is None or best_ask is None:
                flags.add("missing_orderbook_spread")
            bid_ask_imbalance = self._imbalance(bid_size=bid_size, ask_size=ask_size)

            liquidity = max(self._to_float(market_row.get("liquidity_usd")) or 0.0, 0.0)
            volume_24h = max(self._to_float(market_row.get("volume_24h_usd")) or 0.0, 0.0)
            trade_count_24h, trade_size_sum_24h, signed_flow_24h = self._trade_window_features(
                trades=trades,
                decision_timestamp=decision_ts,
            )

            close_ts = self._resolve_close_timestamp(market_row)
            if close_ts is None:
                flags.add("missing_close_timestamp")
            hours_to_close = self._hours_to_close(decision_ts, close_ts)

            move_1h, has_1h = self._price_move(snapshots, decision_ts, timedelta(hours=1))
            move_24h, has_24h = self._price_move(snapshots, decision_ts, timedelta(hours=24))
            if not has_1h:
                flags.add("price_move_1h_fallback")
            if not has_24h:
                flags.add("price_move_24h_fallback")
            volatility_24h = self._realized_volatility(snapshots, decision_ts, timedelta(hours=24))

            category = str(market_row.get("category") or "").strip() or "unknown"
            event_id = str(market_row.get("event_id") or "").strip()
            category_hash_bucket = self._category_hash_bucket(category)
            event_count = event_market_count.get(event_id, 0)
            total_event_liquidity = event_total_liquidity.get(event_id, 0.0)
            event_liquidity_share = liquidity / total_event_liquidity if total_event_liquidity > 0.0 else 0.0

            research_features = self._research_features(
                points=research_points,
                decision_timestamp=decision_ts,
                market_title=str(market_row.get("question") or market_row.get("title") or ""),
                market_category=category,
                event_context=event_id,
            )
            if research_features["f_research_findings_count"] == 0:
                flags.add("missing_research_features")

            rows.append(
                {
                    "row_id": row_id,
                    "feature_schema_version": self.schema_version,
                    "market_id": market_id,
                    "event_id": event_id,
                    "category": category,
                    "decision_timestamp_utc": decision_iso,
                    "label_yes": label_yes,
                    "data_quality_flags": sorted(flags),
                    "f_market_yes_price": round(yes_price, 8),
                    "f_market_no_price": round(no_price, 8),
                    "f_market_price_logit": round(market_logit, 8),
                    "f_market_price_distance_0_5": round(distance_0_5, 8),
                    "f_spread_bps": round(spread_bps, 8),
                    "f_liquidity_usd": round(liquidity, 8),
                    "f_bid_ask_imbalance": round(bid_ask_imbalance, 8),
                    "f_volume_24h_usd": round(volume_24h, 8),
                    "f_trades_count_24h": int(trade_count_24h),
                    "f_trade_size_sum_24h": round(trade_size_sum_24h, 8),
                    "f_trade_signed_flow_24h": round(signed_flow_24h, 8),
                    "f_hours_to_close": round(hours_to_close, 8),
                    "f_decision_weekday": int(decision_ts.weekday()),
                    "f_decision_hour_utc": int(decision_ts.hour),
                    "f_price_move_1h": round(move_1h, 8),
                    "f_price_move_24h": round(move_24h, 8),
                    "f_momentum_24h": round(move_24h, 8),
                    "f_realized_volatility_24h": round(volatility_24h, 8),
                    "f_category_hash_bucket": int(category_hash_bucket),
                    "f_event_market_count": int(event_count),
                    "f_event_liquidity_share": round(event_liquidity_share, 8),
                    **research_features,
                }
            )

        rows.sort(key=lambda row: (str(row.get("decision_timestamp_utc", "")), str(row.get("market_id", ""))))
        storage.write_rows(rows)
        storage.write_schema(
            {
                **FEATURE_SCHEMA.to_dict(),
                "generated_at_utc": datetime.now(UTC).isoformat(),
            }
        )
        manifest_path = storage.write_manifest(
            {
                "dataset_id": effective_dataset_id,
                "corpus_id": effective_corpus_id,
                "schema_version": self.schema_version,
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "rows_path": str(layout.rows_path),
                "schema_path": str(layout.schema_path),
                "counts": {
                    "market_rows_total": len(market_rows),
                    "rows_written": len(rows),
                    "skipped_missing_decision_timestamp": skipped_missing_decision,
                    "skipped_unresolved_or_ambiguous": skipped_unresolved,
                    "duplicate_rows": duplicate_rows,
                },
                "warnings": warnings,
            }
        )
        return FeatureBuildSummary(
            dataset_id=effective_dataset_id,
            corpus_id=effective_corpus_id,
            schema_version=self.schema_version,
            rows_path=layout.rows_path,
            schema_path=layout.schema_path,
            manifest_path=manifest_path,
            rows_written=len(rows),
            skipped_missing_decision_timestamp=skipped_missing_decision,
            skipped_unresolved_or_ambiguous=skipped_unresolved,
            duplicate_rows=duplicate_rows,
            warnings=tuple(warnings),
        )

    def inspect_feature_schema(self, *, dataset_id: str | None = None) -> FeatureSchemaInspection:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = FeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
        )
        storage = FeatureDatasetStorage(layout)
        schema_payload = storage.read_schema()
        columns = self._columns_from_schema_payload(schema_payload)
        if not columns:
            columns = FEATURE_SCHEMA.columns
        return FeatureSchemaInspection(
            dataset_id=effective_dataset_id,
            schema_version=self.schema_version,
            schema_path=layout.schema_path,
            rows_path=layout.rows_path,
            manifest_path=layout.manifest_path,
            schema_exists=layout.schema_path.exists(),
            rows_exist=layout.rows_path.exists(),
            rows_count=storage.row_count(),
            columns=columns,
        )

    def verify_feature_parity(self, *, dataset_id: str | None = None) -> FeatureParityVerification:
        inspection = self.inspect_feature_schema(dataset_id=dataset_id)
        layout = FeatureDatasetLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=inspection.dataset_id,
            schema_version=self.schema_version,
        )
        storage = FeatureDatasetStorage(layout)
        errors: list[str] = []
        warnings: list[str] = []
        expected_columns = {column.name for column in inspection.columns}
        if not inspection.schema_exists:
            errors.append(f"feature schema file missing: {inspection.schema_path}")
        rows = storage.read_rows()
        if not rows:
            warnings.append("feature_rows.jsonl is empty")
        missing_columns = 0
        extra_columns = 0
        version_mismatch = 0
        invalid_decision_timestamp = 0
        invalid_label = 0
        for row in rows:
            keys = set(row.keys())
            row_missing = expected_columns - keys
            row_extra = keys - expected_columns
            if row_missing:
                missing_columns += len(row_missing)
            if row_extra:
                extra_columns += len(row_extra)
            if str(row.get("feature_schema_version") or "").strip() != self.schema_version:
                version_mismatch += 1
            if parse_datetime_utc(row.get("decision_timestamp_utc")) is None:
                invalid_decision_timestamp += 1
            label = row.get("label_yes")
            if label not in {0, 1}:
                invalid_label += 1
        if missing_columns > 0:
            errors.append(f"rows_missing_columns_total={missing_columns}")
        if extra_columns > 0:
            errors.append(f"rows_extra_columns_total={extra_columns}")
        if version_mismatch > 0:
            errors.append(f"rows_with_schema_version_mismatch={version_mismatch}")
        if invalid_decision_timestamp > 0:
            errors.append(f"rows_with_invalid_decision_timestamp={invalid_decision_timestamp}")
        if invalid_label > 0:
            errors.append(f"rows_with_invalid_label_yes={invalid_label}")
        details = {
            "rows_path": str(layout.rows_path),
            "schema_path": str(layout.schema_path),
            "manifest_path": str(layout.manifest_path),
            "rows_count": len(rows),
            "expected_column_count": len(expected_columns),
            "missing_columns_total": missing_columns,
            "extra_columns_total": extra_columns,
            "schema_version_mismatch_rows": version_mismatch,
            "invalid_decision_timestamp_rows": invalid_decision_timestamp,
            "invalid_label_rows": invalid_label,
        }
        return FeatureParityVerification(
            dataset_id=inspection.dataset_id,
            schema_version=self.schema_version,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _event_stats(self, market_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, int], dict[str, float]]:
        counts: dict[str, int] = {}
        liquidity: dict[str, float] = {}
        for row in market_rows:
            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                continue
            counts[event_id] = counts.get(event_id, 0) + 1
            liquidity[event_id] = liquidity.get(event_id, 0.0) + max(self._to_float(row.get("liquidity_usd")) or 0.0, 0.0)
        return counts, liquidity

    def _evidence_by_market_and_decision(self, *, corpus_id: str) -> dict[tuple[str, str], tuple[_EvidencePoint, ...]]:
        evidence_path = self.research_corpus_base_dir / corpus_id / "normalized" / "evidence_findings.jsonl"
        if not evidence_path.exists():
            return {}
        grouped: dict[tuple[str, str], list[_EvidencePoint]] = {}
        for row in self._read_jsonl(evidence_path):
            market_id = str(row.get("market_id") or "").strip()
            decision_ts = parse_datetime_utc(row.get("decision_timestamp_utc"))
            published_at = parse_datetime_utc(row.get("published_at_utc"))
            if not market_id or decision_ts is None or published_at is None:
                continue
            if published_at > decision_ts:
                continue
            sentiment = self._to_float(row.get("sentiment"))
            credibility = self._to_float(row.get("credibility"))
            if sentiment is None or credibility is None:
                continue
            source_name = str(row.get("source_name") or "").strip()
            source_type = str(row.get("source_type") or "").strip()
            summary = str(row.get("summary") or "").strip()
            query = str(row.get("query") or "").strip()
            relevance_hint_raw = self._to_float(row.get("market_relevance_score"))
            relevance_hint = None
            if relevance_hint_raw is not None:
                relevance_hint = clamp(relevance_hint_raw, lower=0.0, upper=1.0)
            parsed_source_type = self._parse_source_type(source_type)
            source_identity = source_name or source_type or "unknown_source"
            key = (market_id, decision_ts.isoformat())
            grouped.setdefault(key, []).append(
                _EvidencePoint(
                    summary=summary,
                    sentiment=clamp(sentiment, lower=-1.0, upper=1.0),
                    credibility=clamp(credibility, lower=0.01, upper=1.0),
                    source_identity=source_identity,
                    source_name=source_name or "unknown_source",
                    source_type=parsed_source_type,
                    query=query,
                    relevance_hint=relevance_hint,
                    published_at_utc=published_at,
                )
            )
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _research_features(
        *,
        points: Sequence[_EvidencePoint],
        decision_timestamp: datetime,
        market_title: str,
        market_category: str,
        event_context: str,
    ) -> dict[str, float | int]:
        if not points:
            return {
                "f_research_findings_count": 0,
                "f_research_weighted_sentiment": 0.0,
                "f_research_evidence_strength": 0.0,
                "f_research_avg_credibility": 0.0,
                "f_research_disagreement": 0.0,
                "f_research_source_diversity": 0.0,
                "f_research_contradiction_rate": 0.0,
                "f_research_conflict_score": 0.0,
                "f_research_freshness_hours": 0.0,
                "f_research_market_relevance": 0.0,
                "f_research_timeliness_decay": 0.0,
                "f_research_source_credibility_prior": 0.0,
                "f_research_entity_event_alignment": 0.0,
                "f_research_evidence_novelty": 0.0,
                "f_research_effective_credibility": 0.0,
            }
        bundle = build_research_feature_bundle(
            market_title=market_title,
            market_category=market_category,
            event_context=event_context,
            decision_timestamp_utc=decision_timestamp,
            evidence_points=tuple(
                ResearchEvidencePoint(
                    summary=point.summary,
                    sentiment=point.sentiment,
                    credibility=point.credibility,
                    source_name=point.source_name,
                    source_type=point.source_type,
                    published_at_utc=point.published_at_utc,
                    query=point.query,
                    relevance_hint=point.relevance_hint,
                )
                for point in points
            ),
        )
        return bundle.to_feature_row_dict()

    @staticmethod
    def _parse_source_type(raw: str) -> SourceType:
        normalized = raw.strip().upper()
        if normalized in {"TWITTER", "REDDIT", "RSS", "OFFICIAL", "MANUAL"}:
            return SourceType(normalized)
        return SourceType.RSS

    @staticmethod
    def _hours_to_close(decision_timestamp: datetime, close_timestamp: datetime | None) -> float:
        if close_timestamp is None:
            return 0.0
        delta_hours = (close_timestamp - decision_timestamp).total_seconds() / 3600.0
        return max(delta_hours, 0.0)

    @staticmethod
    def _price_move(
        snapshots: Sequence[tuple[datetime, float]],
        decision_timestamp: datetime,
        lookback: timedelta,
    ) -> tuple[float, bool]:
        current = FeatureDatasetBuilderService._last_price_at_or_before(snapshots, decision_timestamp)
        if current is None:
            return 0.0, False
        baseline = FeatureDatasetBuilderService._last_price_at_or_before(snapshots, decision_timestamp - lookback)
        if baseline is None:
            return 0.0, False
        return current - baseline, True

    @staticmethod
    def _realized_volatility(
        snapshots: Sequence[tuple[datetime, float]],
        decision_timestamp: datetime,
        window: timedelta,
    ) -> float:
        start = decision_timestamp - window
        eligible = [price for ts, price in snapshots if start <= ts <= decision_timestamp]
        if len(eligible) < 2:
            return 0.0
        returns: list[float] = []
        previous = eligible[0]
        for price in eligible[1:]:
            if previous <= 0.0 or price <= 0.0:
                previous = price
                continue
            returns.append(math.log(price / previous))
            previous = price
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / len(returns)
        return math.sqrt(max(variance, 0.0))

    @staticmethod
    def _trade_window_features(
        *,
        trades: Sequence[tuple[datetime, float, float]],
        decision_timestamp: datetime,
    ) -> tuple[int, float, float]:
        start = decision_timestamp - timedelta(hours=24)
        count = 0
        size_sum = 0.0
        signed_flow = 0.0
        for timestamp, size, sign in trades:
            if timestamp > decision_timestamp or timestamp < start:
                continue
            count += 1
            size_sum += size
            signed_flow += size * sign
        return count, size_sum, signed_flow

    @staticmethod
    def _imbalance(*, bid_size: float | None, ask_size: float | None) -> float:
        if bid_size is None or ask_size is None:
            return 0.0
        denom = bid_size + ask_size
        if denom <= 0.0:
            return 0.0
        return clamp((bid_size - ask_size) / denom, lower=-1.0, upper=1.0)

    @staticmethod
    def _last_price_at_or_before(
        snapshots: Sequence[tuple[datetime, float]],
        decision_timestamp: datetime,
    ) -> float | None:
        candidate: float | None = None
        for timestamp, price in snapshots:
            if timestamp > decision_timestamp:
                break
            candidate = price
        return candidate

    @staticmethod
    def _last_orderbook_at_or_before(
        rows: Sequence[tuple[datetime, float | None, float | None, float | None, float | None]],
        decision_timestamp: datetime,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        best: tuple[float | None, float | None, float | None, float | None] = (None, None, None, None)
        for timestamp, bid, ask, bid_size, ask_size in rows:
            if timestamp > decision_timestamp:
                break
            best = (bid, ask, bid_size, ask_size)
        return best

    @staticmethod
    def _resolution_by_market(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        payload: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            if market_id:
                payload[market_id] = row
        return payload

    @staticmethod
    def _snapshot_series(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[tuple[datetime, float], ...]]:
        grouped: dict[str, list[tuple[datetime, float]]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            ts = parse_datetime_utc(row.get("snapshot_at_utc"))
            yes_price = FeatureDatasetBuilderService._to_float(row.get("yes_price"))
            if not market_id or ts is None or yes_price is None:
                continue
            grouped.setdefault(market_id, []).append((ts, clamp(yes_price, lower=0.0, upper=1.0)))
        return {key: tuple(sorted(value, key=lambda item: item[0])) for key, value in grouped.items()}

    @staticmethod
    def _orderbook_series(
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, tuple[tuple[datetime, float | None, float | None, float | None, float | None], ...]]:
        grouped: dict[str, list[tuple[datetime, float | None, float | None, float | None, float | None]]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            ts = parse_datetime_utc(row.get("snapshot_at_utc"))
            if not market_id or ts is None:
                continue
            grouped.setdefault(market_id, []).append(
                (
                    ts,
                    FeatureDatasetBuilderService._to_float(row.get("best_bid")),
                    FeatureDatasetBuilderService._to_float(row.get("best_ask")),
                    FeatureDatasetBuilderService._to_float(row.get("bid_size")),
                    FeatureDatasetBuilderService._to_float(row.get("ask_size")),
                )
            )
        return {key: tuple(sorted(value, key=lambda item: item[0])) for key, value in grouped.items()}

    @staticmethod
    def _trade_series(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[tuple[datetime, float, float], ...]]:
        grouped: dict[str, list[tuple[datetime, float, float]]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            ts = parse_datetime_utc(row.get("timestamp_utc"))
            if not market_id or ts is None:
                continue
            size = max(FeatureDatasetBuilderService._to_float(row.get("size")) or 0.0, 0.0)
            side = str(row.get("side") or "").strip().upper()
            sign = 0.0
            if side in {"BUY", "BID", "YES"}:
                sign = 1.0
            elif side in {"SELL", "ASK", "NO"}:
                sign = -1.0
            grouped.setdefault(market_id, []).append((ts, size, sign))
        return {key: tuple(sorted(value, key=lambda item: item[0])) for key, value in grouped.items()}

    @staticmethod
    def _resolve_decision_timestamp(row: Mapping[str, Any]) -> datetime | None:
        for key in ("decision_timestamp_utc", "close_at_utc", "close_at", "market_close_at_utc", "resolved_at_utc"):
            parsed = parse_datetime_utc(row.get(key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _resolve_close_timestamp(row: Mapping[str, Any]) -> datetime | None:
        for key in ("close_at_utc", "close_at", "market_close_at_utc"):
            parsed = parse_datetime_utc(row.get(key))
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _resolve_outcome(market_row: Mapping[str, Any], resolution_row: Mapping[str, Any] | None) -> str:
        market_outcome = str(market_row.get("resolved_outcome") or "").strip().upper()
        if market_outcome in {"YES", "NO"}:
            return market_outcome
        if resolution_row is None:
            return ""
        resolution_outcome = str(resolution_row.get("resolved_outcome") or "").strip().upper()
        if resolution_outcome in {"YES", "NO"}:
            return resolution_outcome
        return ""

    @staticmethod
    def _category_hash_bucket(category: str) -> int:
        if not category:
            return 0
        payload = category.encode("utf-8")
        digest = 0
        for byte in payload:
            digest = ((digest * 31) + int(byte)) % 2_147_483_647
        return digest % 128

    @staticmethod
    def _columns_from_schema_payload(schema_payload: Mapping[str, Any]) -> tuple[FeatureColumn, ...]:
        raw_columns = schema_payload.get("columns")
        if not isinstance(raw_columns, list):
            return ()
        columns: list[FeatureColumn] = []
        for row in raw_columns:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("name") or "").strip()
            dtype = str(row.get("dtype") or "").strip()
            group = str(row.get("group") or "").strip()
            description = str(row.get("description") or "").strip()
            if not name or not dtype:
                continue
            columns.append(
                FeatureColumn(
                    name=name,
                    dtype=dtype,
                    group=group or "unknown",
                    description=description,
                )
            )
        return tuple(columns)

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if isinstance(raw, dict):
                    rows.append(raw)
        return rows

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None
