from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.strategy_research.features import FeatureDatasetBuilderService


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_feature_dataset_builder_is_leakage_safe_and_versioned(tmp_path: Path) -> None:
    historical_base = tmp_path / "historical"
    dataset_id = "ds-1"
    normalized = historical_base / dataset_id / "normalized"
    _write_jsonl(
        normalized / "markets.jsonl",
        [
            {
                "market_id": "m-1",
                "event_id": "e-1",
                "category": "politics",
                "close_at_utc": "2026-01-10T12:00:00+00:00",
                "resolved_outcome": "YES",
                "liquidity_usd": 10_000,
                "volume_24h_usd": 2_000,
            }
        ],
    )
    _write_jsonl(
        normalized / "resolutions.jsonl",
        [
            {
                "market_id": "m-1",
                "resolved_outcome": "YES",
                "resolved_at_utc": "2026-01-11T00:00:00+00:00",
            }
        ],
    )
    _write_jsonl(
        normalized / "market_snapshots.jsonl",
        [
            {"market_id": "m-1", "snapshot_at_utc": "2026-01-10T11:00:00+00:00", "yes_price": 0.55},
            {"market_id": "m-1", "snapshot_at_utc": "2026-01-10T13:00:00+00:00", "yes_price": 0.90},
        ],
    )
    _write_jsonl(
        normalized / "orderbook_snapshots.jsonl",
        [
            {
                "market_id": "m-1",
                "snapshot_at_utc": "2026-01-10T11:00:00+00:00",
                "best_bid": 0.53,
                "best_ask": 0.57,
                "bid_size": 50,
                "ask_size": 40,
            },
            {
                "market_id": "m-1",
                "snapshot_at_utc": "2026-01-10T13:00:00+00:00",
                "best_bid": 0.05,
                "best_ask": 0.95,
                "bid_size": 1,
                "ask_size": 99,
            },
        ],
    )
    _write_jsonl(
        normalized / "trades.jsonl",
        [
            {
                "market_id": "m-1",
                "timestamp_utc": "2026-01-10T11:30:00+00:00",
                "size": 10,
                "side": "BUY",
            },
            {
                "market_id": "m-1",
                "timestamp_utc": "2026-01-10T13:30:00+00:00",
                "size": 999,
                "side": "BUY",
            },
        ],
    )

    corpus_base = tmp_path / "corpus"
    _write_jsonl(
        corpus_base / "corp-1" / "normalized" / "evidence_findings.jsonl",
        [
            {
                "market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "source_name": "src-aligned",
                "source_type": "RSS",
                "sentiment": 0.8,
                "credibility": 0.9,
                "published_at_utc": "2026-01-10T10:00:00+00:00",
            },
            {
                "market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "source_name": "src-post",
                "source_type": "RSS",
                "sentiment": -0.8,
                "credibility": 0.9,
                "published_at_utc": "2026-01-10T14:00:00+00:00",
            },
        ],
    )

    service = FeatureDatasetBuilderService(
        historical_base_dir=historical_base,
        research_corpus_base_dir=corpus_base,
        default_dataset_id=dataset_id,
        default_corpus_id="corp-1",
    )
    summary = service.build_feature_dataset()
    assert summary.rows_written == 1
    assert summary.schema_version == "v1"

    rows = [json.loads(line) for line in summary.rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["feature_schema_version"] == "v1"
    assert row["label_yes"] == 1
    assert row["f_market_yes_price"] == 0.55
    assert row["f_trades_count_24h"] == 1
    assert row["f_trade_size_sum_24h"] == 10.0
    assert row["f_research_findings_count"] == 1
    assert row["f_research_weighted_sentiment"] > 0.0
    assert row["f_research_market_relevance"] >= 0.0
    assert row["f_research_timeliness_decay"] > 0.0
    assert row["f_research_conflict_score"] >= 0.0
    assert row["f_research_entity_event_alignment"] >= 0.0
    assert row["f_research_evidence_novelty"] >= 0.0

    inspection = service.inspect_feature_schema()
    assert inspection.schema_exists is True
    assert inspection.rows_count == 1

    verification = service.verify_feature_parity()
    assert verification.ok is True


def test_feature_parity_detects_column_mismatch(tmp_path: Path) -> None:
    historical_base = tmp_path / "historical"
    dataset_id = "ds-1"
    normalized = historical_base / dataset_id / "normalized"
    _write_jsonl(
        normalized / "markets.jsonl",
        [
            {
                "market_id": "m-1",
                "event_id": "e-1",
                "category": "general",
                "close_at_utc": "2026-01-10T12:00:00+00:00",
                "resolved_outcome": "NO",
            }
        ],
    )
    _write_jsonl(
        normalized / "resolutions.jsonl",
        [{"market_id": "m-1", "resolved_outcome": "NO"}],
    )
    service = FeatureDatasetBuilderService(
        historical_base_dir=historical_base,
        research_corpus_base_dir=tmp_path / "corpus",
        default_dataset_id=dataset_id,
        default_corpus_id="corp-1",
    )
    summary = service.build_feature_dataset()
    rows = [json.loads(line) for line in summary.rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    row.pop("f_market_yes_price", None)
    row["extra_feature"] = 1
    summary.rows_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    verification = service.verify_feature_parity()
    assert verification.ok is False
    assert any("rows_missing_columns_total" in error for error in verification.errors)
    assert any("rows_extra_columns_total" in error for error in verification.errors)
