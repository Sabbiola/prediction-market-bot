from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.strategy_research.linkage import EvidenceMarketLinkageService, LinkageState


def _write_dataset(base_dir: Path, dataset_id: str, *, markets: list[dict[str, object]], events: list[dict[str, object]]) -> None:
    normalized_dir = base_dir / dataset_id / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "markets.jsonl").write_text(
        "\n".join(json.dumps(row) for row in markets) + "\n",
        encoding="utf-8",
    )
    (normalized_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(row) for row in events) + "\n",
        encoding="utf-8",
    )


def _write_news_corpus(base_dir: Path, corpus_id: str, *, rows: list[dict[str, object]]) -> None:
    normalized_dir = base_dir / corpus_id / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "news_articles.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def _load_linkage_results(linkage_root: Path) -> list[dict[str, object]]:
    path = linkage_root / "normalized" / "linkage_results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_service(tmp_path: Path, *, alias_map: dict[str, tuple[str, ...]] | None = None, max_staleness_days: int = 45, ambiguity_margin: float = 0.03) -> EvidenceMarketLinkageService:
    return EvidenceMarketLinkageService(
        base_dir=tmp_path / "linkage",
        historical_base_dir=tmp_path / "historical",
        news_corpus_base_dir=tmp_path / "news",
        reddit_corpus_base_dir=tmp_path / "reddit",
        x_corpus_base_dir=tmp_path / "x",
        default_linkage_id="it-linkage",
        default_dataset_id="it-ds",
        default_news_corpus_id="it-news",
        default_reddit_corpus_id="it-reddit",
        default_x_corpus_id="it-x",
        enabled_source_classes=("news_rss_web",),
        max_staleness_days=max_staleness_days,
        similarity_threshold=0.15,
        ambiguity_margin=ambiguity_margin,
        max_candidates_per_evidence=5,
        alias_map=alias_map or {},
    )


def test_linkage_clear_positive_match(tmp_path: Path) -> None:
    _write_dataset(
        tmp_path / "historical",
        "it-ds",
        markets=[
            {
                "market_id": "m-weather",
                "event_id": "e-weather",
                "question": "Will a hurricane make landfall in Florida this week?",
                "category": "weather",
                "close_at_utc": "2026-08-02T00:00:00+00:00",
                "resolved_at_utc": "2026-08-03T00:00:00+00:00",
            }
        ],
        events=[{"event_id": "e-weather", "title": "Florida hurricane watch"}],
    )
    _write_news_corpus(
        tmp_path / "news",
        "it-news",
        rows=[
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "dedup_key": "news-1",
                "source_record_id": "rec-1",
                "title": "Florida prepares as hurricane expected to make landfall",
                "summary": "Emergency services are preparing in Florida.",
                "article_url": "https://example.test/weather-1",
                "published_at_utc": "2026-08-01T10:00:00+00:00",
                "fetched_at_utc": "2026-08-01T11:00:00+00:00",
            }
        ],
    )
    service = _build_service(tmp_path)
    summary = service.build_linkage()
    assert summary.linked_count == 1
    assert summary.ambiguous_count == 0
    assert summary.unresolved_count == 0
    rows = _load_linkage_results(summary.linkage_root)
    assert len(rows) == 1
    assert rows[0]["state"] == LinkageState.LINKED.value
    assert rows[0]["matched_market_id"] == "m-weather"


def test_linkage_marks_ambiguous_when_candidates_are_too_close(tmp_path: Path) -> None:
    _write_dataset(
        tmp_path / "historical",
        "it-ds",
        markets=[
            {
                "market_id": "m-a",
                "event_id": "e-rome",
                "question": "Will candidate A win the Rome mayor election?",
                "category": "politics",
                "close_at_utc": "2026-06-15T00:00:00+00:00",
            },
            {
                "market_id": "m-b",
                "event_id": "e-rome",
                "question": "Will candidate B win the Rome mayor election?",
                "category": "politics",
                "close_at_utc": "2026-06-15T00:00:00+00:00",
            },
        ],
        events=[{"event_id": "e-rome", "title": "Rome mayor election"}],
    )
    _write_news_corpus(
        tmp_path / "news",
        "it-news",
        rows=[
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "dedup_key": "news-2",
                "source_record_id": "rec-2",
                "title": "Rome mayor election is expected to be very close",
                "summary": "Both candidates are neck and neck.",
                "article_url": "https://example.test/politics-1",
                "published_at_utc": "2026-06-10T10:00:00+00:00",
                "fetched_at_utc": "2026-06-10T11:00:00+00:00",
            }
        ],
    )
    service = _build_service(tmp_path, ambiguity_margin=0.20)
    summary = service.build_linkage()
    assert summary.ambiguous_count == 1
    rows = _load_linkage_results(summary.linkage_root)
    assert rows[0]["state"] == LinkageState.AMBIGUOUS.value
    top_candidates = rows[0]["top_candidates"]
    assert isinstance(top_candidates, list)
    assert len(top_candidates) >= 2


def test_linkage_rejects_stale_evidence(tmp_path: Path) -> None:
    _write_dataset(
        tmp_path / "historical",
        "it-ds",
        markets=[
            {
                "market_id": "m-stale",
                "event_id": "e-stale",
                "question": "Will inflation exceed 5% by December 2026?",
                "category": "macro",
                "close_at_utc": "2026-12-01T00:00:00+00:00",
            }
        ],
        events=[{"event_id": "e-stale", "title": "Inflation outlook"}],
    )
    _write_news_corpus(
        tmp_path / "news",
        "it-news",
        rows=[
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "dedup_key": "news-3",
                "source_record_id": "rec-3",
                "title": "Inflation outlook remains elevated",
                "summary": "Analysts discuss inflation expectations.",
                "article_url": "https://example.test/macro-1",
                "published_at_utc": "2025-01-01T00:00:00+00:00",
                "fetched_at_utc": "2025-01-01T01:00:00+00:00",
            }
        ],
    )
    service = _build_service(tmp_path, max_staleness_days=30)
    summary = service.build_linkage()
    assert summary.stale_count == 1
    rows = _load_linkage_results(summary.linkage_root)
    assert rows[0]["state"] == LinkageState.STALE_EVIDENCE.value


def test_linkage_applies_alias_resolution(tmp_path: Path) -> None:
    _write_dataset(
        tmp_path / "historical",
        "it-ds",
        markets=[
            {
                "market_id": "m-alias",
                "event_id": "e-alias",
                "question": "Will the United States raise interest rates in June?",
                "category": "macro",
                "close_at_utc": "2026-06-20T00:00:00+00:00",
            }
        ],
        events=[{"event_id": "e-alias", "title": "United States monetary policy"}],
    )
    _write_news_corpus(
        tmp_path / "news",
        "it-news",
        rows=[
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "dedup_key": "news-4",
                "source_record_id": "rec-4",
                "title": "USA may raise interest rates in June",
                "summary": "Economists debate the likely decision.",
                "article_url": "https://example.test/macro-2",
                "published_at_utc": "2026-06-15T08:00:00+00:00",
                "fetched_at_utc": "2026-06-15T09:00:00+00:00",
            }
        ],
    )
    service = _build_service(tmp_path, alias_map={"united states": ("usa",)})
    summary = service.build_linkage()
    assert summary.linked_count == 1
    rows = _load_linkage_results(summary.linkage_root)
    assert rows[0]["state"] == LinkageState.LINKED.value
    assert "united states" in rows[0]["matched_aliases"]
