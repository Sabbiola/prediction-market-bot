from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.strategy_research.llm_enrichment import (
    AltDataLlmEnrichmentService,
    DeterministicEnrichmentProvider,
)


class _InvalidSchemaProvider:
    provider_name = "invalid_schema_provider"
    model_id = "invalid-schema-v1"

    def enrich(self, record: object, *, prompt_version: str) -> Mapping[str, Any]:
        del record, prompt_version
        return {
            "relevance_score": "1.8",
            "extracted_claims": ["claim-a", "", "claim-b"],
            "contradiction_score": -0.2,
            "contradiction_indicators": ["however"],
            "novelty_score": "bad-number",
            "catalyst_class": "unknown-class",
            "structured_summary": "x" * 900,
        }


class _FailingProvider:
    provider_name = "failing_provider"
    model_id = "failing-v1"

    def enrich(self, record: object, *, prompt_version: str) -> Mapping[str, Any]:
        del record, prompt_version
        raise RuntimeError("provider unavailable")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _seed_input_dataset(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    linkage_base = tmp_path / "linkage"
    news_base = tmp_path / "news"
    reddit_base = tmp_path / "reddit"
    x_base = tmp_path / "x"

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
                "confidence": 0.74,
                "matched_market_id": "m-1",
                "matched_event_id": "e-1",
                "matched_market_title": "Will policy A pass by Q1?",
                "matched_event_title": "Policy A vote",
                "decision_timestamp_utc": "2026-01-11T00:00:00+00:00",
                "evidence_title": "Policy A gains support",
                "evidence_url": "https://example.test/news/1",
                "published_at_utc": "2026-01-10T00:00:00+00:00",
                "fetched_at_utc": "2026-01-11T01:00:00+00:00",
            },
            {
                "evidence_key": "reddit:reddit-dedup-1",
                "source_class": "reddit",
                "source_name": "reddit_oauth_adapter",
                "source_record_id": "t3_abcd",
                "dedup_key": "reddit-dedup-1",
                "state": "ambiguous",
                "confidence": 0.52,
                "matched_market_id": "m-1",
                "matched_event_id": "e-1",
                "matched_market_title": "Will policy A pass by Q1?",
                "matched_event_title": "Policy A vote",
                "decision_timestamp_utc": "2026-01-11T00:00:00+00:00",
                "evidence_title": "Discussion about policy A",
                "evidence_url": "https://reddit.test/r/worldnews/comments/abcd",
                "published_at_utc": "2026-01-09T12:00:00+00:00",
                "fetched_at_utc": "2026-01-11T01:00:00+00:00",
            },
        ],
    )

    _write_jsonl(
        news_base / "news-1" / "normalized" / "news_articles.jsonl",
        [
            {
                "source_class": "news_rss_web",
                "dedup_key": "news-dedup-1",
                "source_record_id": "news-1",
                "title": "Policy A gains support in senate",
                "summary": "Analysts say policy A may pass.",
                "article_url": "https://example.test/news/1",
                "published_at_utc": "2026-01-10T00:00:00+00:00",
                "fetched_at_utc": "2026-01-11T01:00:00+00:00",
            }
        ],
    )

    _write_jsonl(
        reddit_base / "reddit-1" / "normalized" / "reddit_evidence.jsonl",
        [
            {
                "source_class": "reddit",
                "dedup_key": "reddit-dedup-1",
                "source_record_id": "t3_abcd",
                "title": "Policy A discussion",
                "body": "Some users agree, however others disagree.",
                "permalink_url": "https://reddit.test/r/worldnews/comments/abcd",
                "created_at_utc": "2026-01-09T12:00:00+00:00",
                "fetched_at_utc": "2026-01-11T01:00:00+00:00",
            }
        ],
    )

    _write_jsonl(
        x_base / "x-1" / "normalized" / "x_evidence.jsonl",
        [],
    )

    return linkage_base, news_base, reddit_base, x_base


def test_llm_enrichment_service_deterministic_pipeline(tmp_path: Path) -> None:
    linkage_base, news_base, reddit_base, x_base = _seed_input_dataset(tmp_path)
    service = AltDataLlmEnrichmentService(
        base_dir=tmp_path / "llm-enrichment",
        linkage_base_dir=linkage_base,
        news_corpus_base_dir=news_base,
        reddit_corpus_base_dir=reddit_base,
        x_corpus_base_dir=x_base,
        default_enrichment_id="enr-1",
        default_linkage_id="link-1",
        default_news_corpus_id="news-1",
        default_reddit_corpus_id="reddit-1",
        default_x_corpus_id="x-1",
        enabled=True,
        prompt_version="v1",
        include_states=("linked", "ambiguous"),
        throttle_sec=0.0,
        primary_provider=DeterministicEnrichmentProvider(),
        fallback_provider=DeterministicEnrichmentProvider(),
    )

    summary = service.enrich_alt_data()
    assert summary.records_seen == 2
    assert summary.records_processed == 2
    assert summary.failures == 0
    assert summary.enrichments_persisted == 2

    inspection = service.inspect_enrichment()
    assert inspection.normalized_counts["enrichment_records"] == 2

    output_rows = (tmp_path / "llm-enrichment" / "enr-1" / "normalized" / "enrichment_records.jsonl").read_text(
        encoding="utf-8"
    )
    parsed = [json.loads(line) for line in output_rows.splitlines() if line.strip()]
    assert parsed[0]["status"] == "enriched"
    assert 0.0 <= parsed[0]["output"]["relevance_score"] <= 1.0
    assert isinstance(parsed[0]["output"]["extracted_claims"], list)


def test_llm_enrichment_service_schema_normalization_and_fallback(tmp_path: Path) -> None:
    linkage_base, news_base, reddit_base, x_base = _seed_input_dataset(tmp_path)
    service = AltDataLlmEnrichmentService(
        base_dir=tmp_path / "llm-enrichment",
        linkage_base_dir=linkage_base,
        news_corpus_base_dir=news_base,
        reddit_corpus_base_dir=reddit_base,
        x_corpus_base_dir=x_base,
        default_enrichment_id="enr-2",
        default_linkage_id="link-1",
        default_news_corpus_id="news-1",
        default_reddit_corpus_id="reddit-1",
        default_x_corpus_id="x-1",
        enabled=True,
        prompt_version="v2",
        include_states=("linked", "ambiguous"),
        throttle_sec=0.0,
        primary_provider=_InvalidSchemaProvider(),  # type: ignore[arg-type]
        fallback_provider=DeterministicEnrichmentProvider(),
    )

    summary = service.enrich_alt_data(limit_records=1)
    assert summary.records_processed == 1
    assert summary.schema_violations > 0

    path = tmp_path / "llm-enrichment" / "enr-2" / "normalized" / "enrichment_records.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["status"] == "enriched"
    assert row["output"]["relevance_score"] == 1.0
    assert row["output"]["contradiction_score"] == 0.0
    assert row["output"]["novelty_score"] == 0.5
    assert row["output"]["catalyst_class"] == "other"


def test_llm_enrichment_service_handles_provider_failure_without_fallback(tmp_path: Path) -> None:
    linkage_base, news_base, reddit_base, x_base = _seed_input_dataset(tmp_path)
    service = AltDataLlmEnrichmentService(
        base_dir=tmp_path / "llm-enrichment",
        linkage_base_dir=linkage_base,
        news_corpus_base_dir=news_base,
        reddit_corpus_base_dir=reddit_base,
        x_corpus_base_dir=x_base,
        default_enrichment_id="enr-3",
        default_linkage_id="link-1",
        default_news_corpus_id="news-1",
        default_reddit_corpus_id="reddit-1",
        default_x_corpus_id="x-1",
        enabled=True,
        prompt_version="v1",
        include_states=("linked",),
        throttle_sec=0.0,
        primary_provider=_FailingProvider(),  # type: ignore[arg-type]
        fallback_provider=None,
    )

    summary = service.enrich_alt_data(limit_records=1)
    assert summary.records_processed == 1
    assert summary.failures == 1

    row = json.loads(
        (tmp_path / "llm-enrichment" / "enr-3" / "normalized" / "enrichment_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert row["status"] == "failed"
    assert row["failure_reason"] == "RuntimeError"
    assert row["output"]["structured_summary"] == "No structured summary available."
