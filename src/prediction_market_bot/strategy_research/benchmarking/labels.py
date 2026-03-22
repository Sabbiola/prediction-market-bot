from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .models import LabelBuildSummary, LabelRecord, clamp, parse_datetime_utc


@dataclass(slots=True, frozen=True)
class _ResearchFeatureAggregate:
    findings_count: int
    weighted_sentiment: float
    evidence_strength: float
    disagreement_score: float


class LabelDatasetBuilder:
    def __init__(
        self,
        *,
        dataset_id: str,
        dataset_root: Path,
        corpus_root: Path | None,
        labels_path: Path,
    ) -> None:
        self.dataset_id = dataset_id
        self.dataset_root = dataset_root
        self.corpus_root = corpus_root
        self.labels_path = labels_path
        self.labels_manifest_path = labels_path.parent / "labels_manifest.json"

    def build(self) -> tuple[list[LabelRecord], LabelBuildSummary]:
        markets_path = self.dataset_root / "normalized" / "markets.jsonl"
        resolutions_path = self.dataset_root / "normalized" / "resolutions.jsonl"
        snapshots_path = self.dataset_root / "normalized" / "market_snapshots.jsonl"
        if not markets_path.exists():
            raise FileNotFoundError(f"Historical dataset missing markets file: {markets_path}")
        market_rows = self._read_jsonl(markets_path)
        resolution_map = self._build_resolution_map(resolutions_path)
        snapshots = self._build_snapshot_map(snapshots_path)
        research_map = self._build_research_feature_map()

        warnings: list[str] = []
        records: list[LabelRecord] = []
        skipped_missing_timestamp = 0
        skipped_unresolved = 0
        duplicate_rows = 0
        seen_row_ids: set[str] = set()

        for row in market_rows:
            market_id = str(row.get("market_id") or "").strip()
            if not market_id:
                warnings.append("skipped row in markets.jsonl without market_id")
                continue
            decision_ts = parse_datetime_utc(row.get("close_at_utc")) or parse_datetime_utc(row.get("resolved_at_utc"))
            if decision_ts is None:
                skipped_missing_timestamp += 1
                continue

            outcome = self._resolve_outcome(
                market_row=row,
                resolution_row=resolution_map.get(market_id),
            )
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

            flags: list[str] = []
            market_yes_prob = self._to_float(row.get("yes_price_last"))
            if market_yes_prob is None:
                market_yes_prob = self._resolve_market_yes_from_snapshots(snapshots.get(market_id, ()), decision_ts)
                if market_yes_prob is None:
                    market_yes_prob = 0.5
                    flags.append("market_yes_prob_fallback_0_5")
                else:
                    flags.append("market_yes_prob_from_snapshots")
            market_yes_prob = clamp(market_yes_prob, lower=0.01, upper=0.99)

            momentum = self._resolve_momentum(
                snapshots=snapshots.get(market_id, ()),
                decision_timestamp=decision_ts,
                fallback_market_yes_prob=market_yes_prob,
            )
            if snapshots.get(market_id) in {None, ()}:
                flags.append("momentum_fallback_from_market_price")
            momentum = clamp(momentum, lower=-1.0, upper=1.0)

            liquidity = max(self._to_float(row.get("liquidity_usd")) or 0.0, 0.0)
            volume_24h = max(self._to_float(row.get("volume_24h_usd")) or 0.0, 0.0)
            structure_score = self._structure_score(
                market_yes_prob=market_yes_prob,
                liquidity_usd=liquidity,
                volume_24h_usd=volume_24h,
                momentum=momentum,
            )
            scan_score = clamp((structure_score * 0.70) + (min(abs(momentum) * 2.0, 1.0) * 0.30), lower=0.0, upper=1.0)

            research_key = (market_id, decision_iso)
            research = research_map.get(research_key)
            if research is None:
                flags.append("missing_research_features")
                research = _ResearchFeatureAggregate(
                    findings_count=0,
                    weighted_sentiment=0.0,
                    evidence_strength=0.0,
                    disagreement_score=0.0,
                )

            resolved_at = parse_datetime_utc(row.get("resolved_at_utc"))
            if resolved_at is None:
                resolution_row = resolution_map.get(market_id)
                resolved_at = parse_datetime_utc(resolution_row.get("resolved_at_utc")) if resolution_row else None
            if resolved_at is None:
                flags.append("missing_resolved_at")

            records.append(
                LabelRecord(
                    row_id=row_id,
                    market_id=market_id,
                    event_id=str(row.get("event_id") or "").strip(),
                    category=str(row.get("category") or "").strip() or "unknown",
                    market_title=str(row.get("question") or "").strip() or market_id,
                    decision_timestamp_utc=decision_ts,
                    resolved_at_utc=resolved_at,
                    resolved_outcome=outcome,
                    label_yes=label_yes,
                    market_yes_prob_at_decision=round(market_yes_prob, 6),
                    liquidity_usd=round(liquidity, 6),
                    volume_24h_usd=round(volume_24h, 6),
                    structure_momentum=round(momentum, 6),
                    structure_score=round(structure_score, 6),
                    scan_score=round(scan_score, 6),
                    research_weighted_sentiment=round(research.weighted_sentiment, 6),
                    research_evidence_strength=round(research.evidence_strength, 6),
                    research_disagreement_score=round(research.disagreement_score, 6),
                    research_findings_count=research.findings_count,
                    data_quality_flags=tuple(sorted(set(flags))),
                )
            )

        records = sorted(records, key=lambda item: (item.decision_timestamp_utc, item.market_id))
        self._write_labels(records)
        summary = LabelBuildSummary(
            dataset_id=self.dataset_id,
            labels_path=self.labels_path,
            labels_manifest_path=self.labels_manifest_path,
            total_market_rows=len(market_rows),
            labels_written=len(records),
            skipped_missing_timestamp=skipped_missing_timestamp,
            skipped_unresolved_or_ambiguous=skipped_unresolved,
            duplicate_rows=duplicate_rows,
            warnings=tuple(warnings),
        )
        self._write_manifest(summary=summary, corpus_root=self.corpus_root)
        return records, summary

    def _build_resolution_map(self, resolutions_path: Path) -> dict[str, Mapping[str, Any]]:
        rows = self._read_jsonl(resolutions_path)
        payload: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            if market_id:
                payload[market_id] = row
        return payload

    def _build_snapshot_map(self, snapshots_path: Path) -> dict[str, tuple[tuple[datetime, float], ...]]:
        rows = self._read_jsonl(snapshots_path)
        by_market: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            if not market_id:
                continue
            snapshot_at = parse_datetime_utc(row.get("snapshot_at_utc"))
            yes_price = self._to_float(row.get("yes_price"))
            if snapshot_at is None or yes_price is None:
                continue
            by_market[market_id].append((snapshot_at, clamp(yes_price, lower=0.0, upper=1.0)))
        return {
            market_id: tuple(sorted(values, key=lambda item: item[0]))
            for market_id, values in by_market.items()
        }

    def _build_research_feature_map(self) -> dict[tuple[str, str], _ResearchFeatureAggregate]:
        if self.corpus_root is None:
            return {}
        evidence_path = self.corpus_root / "normalized" / "evidence_findings.jsonl"
        if not evidence_path.exists():
            return {}
        rows = self._read_jsonl(evidence_path)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            market_id = str(row.get("market_id") or "").strip()
            decision_ts = parse_datetime_utc(row.get("decision_timestamp_utc"))
            if not market_id or decision_ts is None:
                continue
            is_aligned = row.get("is_time_aligned")
            if isinstance(is_aligned, bool) and not is_aligned:
                continue
            grouped[(market_id, decision_ts.isoformat())].append(dict(row))

        features: dict[tuple[str, str], _ResearchFeatureAggregate] = {}
        for key, group_rows in grouped.items():
            sentiments: list[float] = []
            credibilities: list[float] = []
            source_types: set[str] = set()
            for row in group_rows:
                sentiment = self._to_float(row.get("sentiment"))
                credibility = self._to_float(row.get("credibility"))
                if sentiment is None or credibility is None:
                    continue
                sentiments.append(clamp(sentiment, lower=-1.0, upper=1.0))
                credibilities.append(clamp(credibility, lower=0.01, upper=1.0))
                source_type = str(row.get("source_type") or "").strip().upper()
                if source_type:
                    source_types.add(source_type)
            if not sentiments or not credibilities:
                continue
            weighted_sentiment = self._weighted_sentiment(sentiments, credibilities)
            evidence_strength = self._evidence_strength(credibilities=credibilities, source_type_count=len(source_types))
            disagreement = self._disagreement_score(
                sentiments=sentiments,
                credibilities=credibilities,
                weighted_sentiment=weighted_sentiment,
            )
            features[key] = _ResearchFeatureAggregate(
                findings_count=len(sentiments),
                weighted_sentiment=weighted_sentiment,
                evidence_strength=evidence_strength,
                disagreement_score=disagreement,
            )
        return features

    @staticmethod
    def _resolve_outcome(*, market_row: Mapping[str, Any], resolution_row: Mapping[str, Any] | None) -> str:
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
    def _resolve_market_yes_from_snapshots(
        snapshots: tuple[tuple[datetime, float], ...],
        decision_timestamp: datetime,
    ) -> float | None:
        eligible = [price for timestamp, price in snapshots if timestamp <= decision_timestamp]
        if not eligible:
            return None
        return eligible[-1]

    @staticmethod
    def _resolve_momentum(
        *,
        snapshots: tuple[tuple[datetime, float], ...],
        decision_timestamp: datetime,
        fallback_market_yes_prob: float,
    ) -> float:
        eligible = [(timestamp, price) for timestamp, price in snapshots if timestamp <= decision_timestamp]
        if len(eligible) >= 2:
            return eligible[-1][1] - eligible[0][1]
        if len(eligible) == 1:
            return eligible[0][1] - 0.5
        return fallback_market_yes_prob - 0.5

    @staticmethod
    def _structure_score(
        *,
        market_yes_prob: float,
        liquidity_usd: float,
        volume_24h_usd: float,
        momentum: float,
    ) -> float:
        liquidity_factor = min(liquidity_usd / 50_000.0, 1.0)
        volume_factor = min(volume_24h_usd / 50_000.0, 1.0)
        momentum_factor = min(abs(momentum) * 2.0, 1.0)
        balance_factor = 1.0 - min(abs(market_yes_prob - 0.5) * 2.0, 1.0)
        value = (
            (liquidity_factor * 0.35)
            + (volume_factor * 0.25)
            + (momentum_factor * 0.20)
            + (balance_factor * 0.20)
        )
        return clamp(value, lower=0.0, upper=1.0)

    @staticmethod
    def _weighted_sentiment(sentiments: list[float], credibilities: list[float]) -> float:
        weighted_sum = 0.0
        total_weight = 0.0
        for sentiment, credibility in zip(sentiments, credibilities, strict=False):
            weight = max(credibility, 0.01)
            weighted_sum += sentiment * weight
            total_weight += weight
        if total_weight <= 0.0:
            return 0.0
        return clamp(weighted_sum / total_weight, lower=-1.0, upper=1.0)

    @staticmethod
    def _evidence_strength(*, credibilities: list[float], source_type_count: int) -> float:
        credibility_avg = sum(credibilities) / len(credibilities)
        count_factor = min(len(credibilities) / 6.0, 1.0)
        diversity_factor = min(source_type_count / 4.0, 1.0)
        value = (credibility_avg * 0.40) + (count_factor * 0.40) + (diversity_factor * 0.20)
        return clamp(value, lower=0.0, upper=1.0)

    @staticmethod
    def _disagreement_score(
        *,
        sentiments: list[float],
        credibilities: list[float],
        weighted_sentiment: float,
    ) -> float:
        if len(sentiments) <= 1:
            return 0.0
        weighted_error = 0.0
        weight_total = 0.0
        for sentiment, credibility in zip(sentiments, credibilities, strict=False):
            weight = max(credibility, 0.01)
            weight_total += weight
            weighted_error += weight * ((sentiment - weighted_sentiment) ** 2)
        if weight_total <= 0.0:
            return 0.0
        variance = weighted_error / weight_total
        return clamp(math.sqrt(variance), lower=0.0, upper=1.0)

    def _write_labels(self, records: list[LabelRecord]) -> None:
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        with self.labels_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), default=str))
                handle.write("\n")

    def _write_manifest(self, *, summary: LabelBuildSummary, corpus_root: Path | None) -> None:
        payload = {
            "dataset_id": summary.dataset_id,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "labels_path": str(summary.labels_path),
            "source_dataset_root": str(self.dataset_root),
            "source_corpus_root": str(corpus_root) if corpus_root is not None else "",
            "counts": {
                "total_market_rows": summary.total_market_rows,
                "labels_written": summary.labels_written,
                "skipped_missing_timestamp": summary.skipped_missing_timestamp,
                "skipped_unresolved_or_ambiguous": summary.skipped_unresolved_or_ambiguous,
                "duplicate_rows": summary.duplicate_rows,
            },
            "warnings": list(summary.warnings),
        }
        self.labels_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.labels_manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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
