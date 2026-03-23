from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.strategy_research.alt_features import AltFeatureDatasetBuilderService


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_alt_feature_dataset_builder_is_timestamp_safe_and_versioned(tmp_path: Path) -> None:
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
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "resolved_outcome": "YES",
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

    linkage_base = tmp_path / "linkage"
    _write_jsonl(
        linkage_base / "link-1" / "normalized" / "linkage_results.jsonl",
        [
            {
                "evidence_key": "news_rss_web:news-dedup-1",
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "source_record_id": "news-1",
                "dedup_key": "news-dedup-1",
                "state": "linked",
                "matched_market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "published_at_utc": "2026-01-10T08:00:00+00:00",
            },
            {
                "evidence_key": "reddit:reddit-dedup-1",
                "source_class": "reddit",
                "source_name": "reddit_oauth_adapter",
                "source_record_id": "t3_abcd",
                "dedup_key": "reddit-dedup-1",
                "state": "ambiguous",
                "matched_market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "published_at_utc": "2026-01-10T11:00:00+00:00",
            },
            {
                "evidence_key": "x:x-dedup-late",
                "source_class": "x",
                "source_name": "x_api_adapter",
                "source_record_id": "x-1",
                "dedup_key": "x-dedup-late",
                "state": "linked",
                "matched_market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "published_at_utc": "2026-01-10T13:00:00+00:00",
            },
            {
                "evidence_key": "x:x-dedup-missing-ts",
                "source_class": "x",
                "source_name": "x_api_adapter",
                "source_record_id": "x-2",
                "dedup_key": "x-dedup-missing-ts",
                "state": "linked",
                "matched_market_id": "m-1",
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "published_at_utc": "",
            },
        ],
    )

    _write_jsonl(
        tmp_path / "news" / "news-1" / "normalized" / "news_articles.jsonl",
        [
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "source_record_id": "news-1",
                "dedup_key": "news-dedup-1",
                "published_at_utc": "2026-01-10T08:00:00+00:00",
                "publisher": "Reuters",
            }
        ],
    )
    _write_jsonl(
        tmp_path / "reddit" / "reddit-1" / "normalized" / "reddit_evidence.jsonl",
        [
            {
                "source_class": "reddit",
                "source_name": "reddit_oauth_adapter",
                "source_record_id": "t3_abcd",
                "dedup_key": "reddit-dedup-1",
                "created_at_utc": "2026-01-10T11:00:00+00:00",
                "subreddit": "worldnews",
                "score": 12,
                "num_comments": 4,
            }
        ],
    )
    _write_jsonl(
        tmp_path / "x" / "x-1" / "normalized" / "x_evidence.jsonl",
        [
            {
                "source_class": "x",
                "source_name": "x_api_adapter",
                "source_record_id": "x-1",
                "dedup_key": "x-dedup-late",
                "created_at_utc": "2026-01-10T13:00:00+00:00",
                "public_metrics": {"like_count": 10, "retweet_count": 2, "reply_count": 1, "quote_count": 1},
            }
        ],
    )
    _write_jsonl(
        tmp_path / "llm" / "enrich-1" / "normalized" / "enrichment_records.jsonl",
        [
            {
                "evidence_key": "news_rss_web:news-dedup-1",
                "status": "ok",
                "created_at_utc": "2026-01-10T09:00:00+00:00",
                "output": {
                    "relevance_score": 0.9,
                    "contradiction_score": 0.2,
                    "novelty_score": 0.6,
                    "catalyst_class": "policy",
                },
            },
            {
                "evidence_key": "reddit:reddit-dedup-1",
                "status": "ok",
                "created_at_utc": "2026-01-10T11:30:00+00:00",
                "output": {
                    "relevance_score": 0.7,
                    "contradiction_score": 0.4,
                    "novelty_score": 0.5,
                    "catalyst_class": "macro",
                },
            },
        ],
    )

    service = AltFeatureDatasetBuilderService(
        historical_base_dir=historical_base,
        linkage_base_dir=linkage_base,
        news_corpus_base_dir=tmp_path / "news",
        reddit_corpus_base_dir=tmp_path / "reddit",
        x_corpus_base_dir=tmp_path / "x",
        llm_enrichment_base_dir=tmp_path / "llm",
        default_dataset_id=dataset_id,
        default_linkage_id="link-1",
        default_news_corpus_id="news-1",
        default_reddit_corpus_id="reddit-1",
        default_x_corpus_id="x-1",
        default_enrichment_id="enrich-1",
    )
    summary = service.build_alt_feature_dataset()
    assert summary.schema_version == "alt-v1"
    assert summary.rows_written == 1
    assert summary.skipped_post_decision_evidence == 1
    assert summary.skipped_missing_published_at == 1

    rows = [json.loads(line) for line in summary.rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["feature_schema_version"] == "alt-v1"
    assert row["decision_timestamp_utc"] == "2026-01-10T12:00:00+00:00"
    assert row["label_yes"] == 1
    assert row["f_alt_evidence_count"] == 2
    assert row["f_alt_linked_evidence_count"] == 1
    assert row["f_alt_news_volume_24h"] == 1
    assert row["f_alt_reddit_mentions_24h"] == 1
    assert row["f_alt_x_mentions_24h"] == 0
    assert row["f_alt_market_linked_coverage"] == 0.5
    assert row["f_alt_enrichment_coverage"] == 1.0
    assert row["f_alt_contradiction_score"] > 0.0
    assert row["f_alt_novelty_score"] > 0.0
    assert row["f_alt_catalyst_strength_score"] > 0.0
    assert row["data_quality_flags"] == []

    inspection = service.inspect_alt_feature_schema()
    assert inspection.schema_exists is True
    assert inspection.rows_count == 1

    verification = service.verify_alt_feature_parity()
    assert verification.ok is True


def test_alt_feature_parity_detects_schema_and_version_mismatch(tmp_path: Path) -> None:
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
                "decision_timestamp_utc": "2026-01-10T12:00:00+00:00",
                "resolved_outcome": "NO",
            }
        ],
    )
    _write_jsonl(normalized / "resolutions.jsonl", [{"market_id": "m-1", "resolved_outcome": "NO"}])
    _write_jsonl(
        tmp_path / "linkage" / "link-1" / "normalized" / "linkage_results.jsonl",
        [],
    )

    service = AltFeatureDatasetBuilderService(
        historical_base_dir=historical_base,
        linkage_base_dir=tmp_path / "linkage",
        news_corpus_base_dir=tmp_path / "news",
        reddit_corpus_base_dir=tmp_path / "reddit",
        x_corpus_base_dir=tmp_path / "x",
        llm_enrichment_base_dir=tmp_path / "llm",
        default_dataset_id=dataset_id,
        default_linkage_id="link-1",
        default_news_corpus_id="news-1",
        default_reddit_corpus_id="reddit-1",
        default_x_corpus_id="x-1",
        default_enrichment_id="enrich-1",
    )
    summary = service.build_alt_feature_dataset()
    row = json.loads(summary.rows_path.read_text(encoding="utf-8").splitlines()[0])
    row.pop("f_alt_news_volume_24h", None)
    row["extra_feature"] = 1
    row["feature_schema_version"] = "alt-v0"
    summary.rows_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    verification = service.verify_alt_feature_parity()
    assert verification.ok is False
    assert any("rows_missing_columns_total" in error for error in verification.errors)
    assert any("rows_extra_columns_total" in error for error in verification.errors)
    assert any("rows_with_schema_version_mismatch" in error for error in verification.errors)
