from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib import parse

import pytest
import yaml

from prediction_market_bot.infrastructure.alt_data import (
    NewsArticleRecord,
    NewsFetchBatch,
    NewsQueryKind,
    RedditEvidenceKind,
    RedditEvidenceRecord,
    RedditFetchPage,
    RedditQueryKind,
    XFetchPage,
    XPostRecord,
    XQueryKind,
)
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.infrastructure.research import SourceFetchBatch
from prediction_market_bot.infrastructure import http_client as http_client_module
from prediction_market_bot.main import main


class _MockHttpResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def __enter__(self) -> "_MockHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            return self._payload
        return self._payload[:amount]


def _set_strategy_research_endpoints(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["historical_data_ingest"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research"),
        "default_dataset_id": "it-ds",
        "markets_endpoint_url": "https://historical.test/markets",
        "events_endpoint_url": "https://historical.test/events/{event_id}",
        "snapshots_endpoint_url": "https://historical.test/markets/{market_id}/snapshots",
        "orderbook_endpoint_url": "https://historical.test/markets/{market_id}/orderbook",
        "trades_endpoint_url": "https://historical.test/markets/{market_id}/trades",
        "resolutions_endpoint_url": "https://historical.test/markets/{market_id}/resolution",
        "page_size": 100,
        "max_pages_per_run": 0,
        "throttle_sec": 0.0,
        "include_orderbook": True,
        "include_trades": True,
    }
    payload["strategy_research"]["research_corpus"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-corpus"),
        "default_corpus_id": "it-corpus",
        "source_dataset_id": "it-ds",
        "enabled_sources": ["wikipedia"],
        "limit_per_source": 5,
        "throttle_sec": 0.0,
        "require_published_at": True,
        "drop_unaligned": True,
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _set_news_ingestion_config(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("alt_data", {})
    payload["alt_data"]["enabled"] = True
    payload["alt_data"]["sources"] = {
        "news_rss_web": {
            "enabled": True,
            "source_class": "news_rss_web",
            "adapter": "rss_news_adapter",
            "endpoint_url": "https://news.google.com/rss",
            "credential_env": "",
            "capabilities": {
                "requires_oauth": False,
                "requires_user_context": False,
                "supports_backfill": True,
                "supports_live_polling": True,
                "supports_search": True,
                "supports_thread_context_expansion": False,
            },
        }
    }
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["news_corpus"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-news"),
        "default_corpus_id": "it-news-corpus",
        "source_id": "news_rss_web",
        "limit_per_query": 10,
        "throttle_sec": 0.0,
        "language": "en-US",
        "region": "US",
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _set_reddit_ingestion_config(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("alt_data", {})
    payload["alt_data"]["enabled"] = True
    payload["alt_data"]["sources"] = {
        "reddit": {
            "enabled": True,
            "source_class": "reddit",
            "adapter": "reddit_oauth_adapter",
            "endpoint_url": "https://oauth.reddit.com",
            "credential_env": "REDDIT_ACCESS_TOKEN",
            "capabilities": {
                "requires_oauth": True,
                "requires_user_context": False,
                "supports_backfill": True,
                "supports_live_polling": True,
                "supports_search": True,
                "supports_thread_context_expansion": True,
            },
        }
    }
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["reddit_corpus"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-reddit"),
        "default_corpus_id": "it-reddit-corpus",
        "source_id": "reddit",
        "limit_per_query": 10,
        "max_pages_per_query": 1,
        "include_comments": True,
        "comment_limit_per_post": 5,
        "throttle_sec": 0.0,
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _set_x_ingestion_config(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("alt_data", {})
    payload["alt_data"]["enabled"] = True
    payload["alt_data"]["sources"] = {
        "x": {
            "enabled": True,
            "source_class": "x",
            "adapter": "x_api_adapter",
            "endpoint_url": "https://api.x.com",
            "credential_env": "X_BEARER_TOKEN",
            "capabilities": {
                "requires_oauth": True,
                "requires_user_context": False,
                "supports_backfill": True,
                "supports_live_polling": True,
                "supports_search": True,
                "supports_thread_context_expansion": True,
            },
        }
    }
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["x_corpus"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-x"),
        "default_corpus_id": "it-x-corpus",
        "source_id": "x",
        "limit_per_query": 10,
        "max_pages_per_query": 1,
        "auth_mode": "user_context",
        "search_endpoint_path": "/2/tweets/search/recent",
        "user_lookup_endpoint_path_template": "/2/users/by/username/{username}",
        "user_posts_endpoint_path_template": "/2/users/{user_id}/tweets",
        "throttle_sec": 0.0,
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _set_linkage_config(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["linkage"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-linkage"),
        "default_linkage_id": "it-linkage",
        "source_dataset_id": "it-ds",
        "news_corpus_id": "it-news-corpus",
        "reddit_corpus_id": "it-reddit-corpus",
        "x_corpus_id": "it-x-corpus",
        "enabled_source_classes": ["news_rss_web"],
        "max_staleness_days": 45,
        "similarity_threshold": 0.15,
        "ambiguity_margin": 0.10,
        "max_candidates_per_evidence": 5,
        "alias_map": {"united states": ["usa"]},
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _set_llm_enrichment_config(app_cfg_path: Path) -> None:
    with app_cfg_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload.setdefault("strategy_research", {})
    payload["strategy_research"]["llm_enrichment"] = {
        "base_dir": str(app_cfg_path.parent / "strategy-research-llm-enrichment"),
        "default_enrichment_id": "it-enrichment",
        "linkage_id": "it-linkage",
        "news_corpus_id": "it-news-corpus",
        "reddit_corpus_id": "it-reddit-corpus",
        "x_corpus_id": "it-x-corpus",
        "enabled": True,
        "provider": "deterministic",
        "model_id": "deterministic-enrichment-v1",
        "prompt_version": "v1",
        "enable_fallback": True,
        "allow_external_provider": False,
        "include_states": ["linked", "ambiguous"],
        "throttle_sec": 0.0,
    }
    app_cfg_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_market_dataset(app_cfg_path: Path) -> Path:
    source_root = app_cfg_path.parent / "strategy-research" / "it-ds" / "normalized"
    source_root.mkdir(parents=True, exist_ok=True)
    markets_path = source_root / "markets.jsonl"
    markets_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "market_id": "m-101",
                        "event_id": "e-101",
                        "question": "Will policy A pass by Q1?",
                        "category": "politics",
                        "close_at_utc": "2026-01-12T00:00:00+00:00",
                        "resolved_at_utc": "2026-01-13T00:00:00+00:00",
                        "resolved_outcome": "YES",
                        "yes_price_last": 0.51,
                        "liquidity_usd": 10000,
                        "volume_24h_usd": 2500,
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return markets_path


def _seed_alt_data_linkage_inputs(app_cfg_path: Path) -> None:
    linkage_root = app_cfg_path.parent / "strategy-research-linkage" / "it-linkage" / "normalized"
    linkage_root.mkdir(parents=True, exist_ok=True)
    linkage_rows = [
        {
            "evidence_key": "news_rss_web:news-dedup-1",
            "source_class": "news_rss_web",
            "source_name": "rss_news_adapter",
            "source_record_id": "news-1",
            "dedup_key": "news-dedup-1",
            "state": "linked",
            "confidence": 0.71,
            "matched_market_id": "m-101",
            "matched_event_id": "e-101",
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
            "confidence": 0.55,
            "matched_market_id": "m-101",
            "matched_event_id": "e-101",
            "matched_market_title": "Will policy A pass by Q1?",
            "matched_event_title": "Policy A vote",
            "decision_timestamp_utc": "2026-01-11T00:00:00+00:00",
            "evidence_title": "Policy A discussion",
            "evidence_url": "https://reddit.test/r/worldnews/comments/abcd",
            "published_at_utc": "2026-01-09T12:00:00+00:00",
            "fetched_at_utc": "2026-01-11T01:00:00+00:00",
        },
    ]
    (linkage_root / "linkage_results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in linkage_rows) + "\n",
        encoding="utf-8",
    )

    news_root = app_cfg_path.parent / "strategy-research-news" / "it-news-corpus" / "normalized"
    news_root.mkdir(parents=True, exist_ok=True)
    (news_root / "news_articles.jsonl").write_text(
        json.dumps(
            {
                "source_class": "news_rss_web",
                "dedup_key": "news-dedup-1",
                "source_record_id": "news-1",
                "title": "Policy A gains support in senate",
                "summary": "Analysts report higher approval odds.",
                "article_url": "https://example.test/news/1",
                "published_at_utc": "2026-01-10T00:00:00+00:00",
                "fetched_at_utc": "2026-01-11T01:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    reddit_root = app_cfg_path.parent / "strategy-research-reddit" / "it-reddit-corpus" / "normalized"
    reddit_root.mkdir(parents=True, exist_ok=True)
    (reddit_root / "reddit_evidence.jsonl").write_text(
        json.dumps(
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
        )
        + "\n",
        encoding="utf-8",
    )

    x_root = app_cfg_path.parent / "strategy-research-x" / "it-x-corpus" / "normalized"
    x_root.mkdir(parents=True, exist_ok=True)
    (x_root / "x_evidence.jsonl").write_text("", encoding="utf-8")

def _seed_benchmark_dataset(app_cfg_path: Path) -> None:
    root = app_cfg_path.parent / "strategy-research" / "it-ds" / "normalized"
    root.mkdir(parents=True, exist_ok=True)
    markets = []
    snapshots = []
    resolutions = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        yes_price = 0.35 + (index * 0.07)
        yes_price = max(min(yes_price, 0.95), 0.05)
        label_yes = 1 if index % 2 == 0 else 0
        markets.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "question": f"Benchmark market {index+1}",
                "category": "politics" if index % 2 == 0 else "macro",
                "close_at_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "resolved_at_utc": f"2026-01-{decision_day+1:02d}T00:00:00+00:00",
                "resolved_outcome": "YES" if label_yes == 1 else "NO",
                "yes_price_last": yes_price,
                "liquidity_usd": 10000 + (index * 1000),
                "volume_24h_usd": 3000 + (index * 500),
            }
        )
        resolutions.append(
            {
                "market_id": market_id,
                "resolved_outcome": "YES" if label_yes == 1 else "NO",
                "resolved_at_utc": f"2026-01-{decision_day+1:02d}T00:00:00+00:00",
            }
        )
        snapshots.extend(
            [
                {
                    "market_id": market_id,
                    "snapshot_at_utc": f"2026-01-{max(decision_day-2,1):02d}T00:00:00+00:00",
                    "yes_price": max(yes_price - 0.05, 0.01),
                },
                {
                    "market_id": market_id,
                    "snapshot_at_utc": f"2026-01-{max(decision_day-1,1):02d}T00:00:00+00:00",
                    "yes_price": yes_price,
                },
            ]
        )
    (root / "markets.jsonl").write_text("\n".join(json.dumps(row) for row in markets) + "\n", encoding="utf-8")
    (root / "resolutions.jsonl").write_text("\n".join(json.dumps(row) for row in resolutions) + "\n", encoding="utf-8")
    (root / "market_snapshots.jsonl").write_text(
        "\n".join(json.dumps(row) for row in snapshots) + "\n",
        encoding="utf-8",
    )


class _FakeResearchSource:
    source_name = "wikipedia-search"

    def fetch_with_meta(
        self,
        market: MarketSnapshot,
        *,
        query_override: str | None = None,
        limit: int = 5,
    ) -> SourceFetchBatch:
        del query_override, limit
        finding = ResearchFinding(
            source_type=SourceType.RSS,
            source_name=self.source_name,
            summary=f"Research for {market.market_id}",
            sentiment=0.1,
            credibility=0.7,
            url=f"https://example.test/{market.market_id}",
            provenance=(
                "query=policy pass q1",
                "fetched_at=2026-01-11T00:00:00+00:00",
                "published_at=2026-01-10T00:00:00+00:00",
                "source=wikipedia-search",
            ),
        )
        return SourceFetchBatch(
            source_name=self.source_name,
            source_type=SourceType.RSS,
            query="policy pass q1",
            raw_count=1,
            normalized_count=1,
            retries_used=0,
            cache_hit=False,
            duration_ms=1.2,
            findings=(finding,),
            raw_records=(
                {
                    "id": f"rec-{market.market_id}",
                    "title": f"Research for {market.market_id}",
                    "published_at": "2026-01-10T00:00:00+00:00",
                },
            ),
        )


def test_strategy_research_cli_backfill_inspect_verify(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)

    def fake_urlopen(req: object, timeout: float) -> _MockHttpResponse:
        del timeout
        url = getattr(req, "full_url", "")
        parsed = parse.urlparse(url)
        path = parsed.path
        if path == "/markets":
            payload = {
                "markets": [
                    {"id": "m1", "event_id": "e1", "question": "q1", "resolution": "YES"},
                    {"id": "m2", "event_id": "e2", "question": "q2", "resolution": "NO"},
                ],
                "next_cursor": None,
            }
            return _MockHttpResponse(json.dumps(payload))
        if path == "/events/e1":
            return _MockHttpResponse(json.dumps({"id": "e1", "title": "event-1", "category": "cat"}))
        if path == "/events/e2":
            return _MockHttpResponse(json.dumps({"id": "e2", "title": "event-2", "category": "cat"}))
        if path.endswith("/snapshots"):
            return _MockHttpResponse(json.dumps([{"timestamp": "2026-01-01T00:00:00Z", "outcomePrices": [0.5, 0.5]}]))
        if path.endswith("/orderbook"):
            return _MockHttpResponse(json.dumps([{"timestamp": "2026-01-01T00:00:00Z", "bestBid": "0.49", "bestAsk": "0.51"}]))
        if path.endswith("/trades"):
            return _MockHttpResponse(json.dumps([{"id": "t1", "timestamp": "2026-01-01T00:00:00Z", "price": "0.5", "size": "10"}]))
        if path.endswith("/resolution"):
            return _MockHttpResponse(json.dumps({"outcome": "YES", "resolutionDate": "2026-01-02T00:00:00Z", "status": "RESOLVED"}))
        raise AssertionError(f"Unexpected URL in strategy research CLI test: {url}")

    monkeypatch.setattr(http_client_module.request, "urlopen", fake_urlopen)

    backfill_exit = main(
        [
            "backfill-historical-markets",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert backfill_exit == 0
    backfill_payload = json.loads(capsys.readouterr().out)
    assert backfill_payload["dataset_id"] == "it-ds"
    assert backfill_payload["markets_processed"] == 2

    inspect_exit = main(
        [
            "inspect-dataset",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["markets"] >= 2
    assert inspect_payload["checkpoint_completed"] is True

    verify_exit = main(
        [
            "verify-dataset",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True


def test_strategy_research_cli_research_corpus_backfill_inspect_verify(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_market_dataset(app_cfg)
    monkeypatch.setattr(
        "prediction_market_bot.cli.commands.strategy_research_commands.build_live_research_sources",
        lambda **_: (_FakeResearchSource(),),
    )

    backfill_exit = main(
        [
            "backfill-research-evidence",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert backfill_exit == 0
    backfill_payload = json.loads(capsys.readouterr().out)
    assert backfill_payload["corpus_id"] == "it-corpus"
    assert backfill_payload["decision_points_processed"] == 1
    assert backfill_payload["normalized_findings_persisted"] == 1

    inspect_exit = main(
        [
            "inspect-research-corpus",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["evidence_findings"] == 1

    verify_exit = main(
        [
            "verify-research-alignment",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True


def test_strategy_research_cli_news_backfill_inspect_verify(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_news_ingestion_config(app_cfg)

    def fake_news_fetch(self: object, query: object, *, limit: int = 50) -> NewsFetchBatch:
        del self, limit
        query_text = str(getattr(query, "value", "WORLD"))
        query_kind = getattr(query, "kind", NewsQueryKind.KEYWORD)
        record = NewsArticleRecord(
            source_id="news_rss_web",
            source_class="news_rss_web",
            source_name="rss_news_adapter",
            query=query_text,
            query_kind=query_kind,
            dedup_key=f"dedup-{query_kind.value}-{query_text.lower()}",
            source_record_id=f"rec-{query_kind.value}-{query_text.lower()}",
            title=f"News for {query_text}",
            summary=f"Summary for {query_text}",
            article_url=f"https://example.test/{query_kind.value}/{query_text.lower()}",
            article_domain="example.test",
            publisher="Example News",
            published_at_utc=datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            source_metadata={"feed_title": "Google News"},
            raw_payload={"query": query_text},
        )
        return NewsFetchBatch(
            source_id="news_rss_web",
            source_name="rss_news_adapter",
            query=query_text,
            query_kind=query_kind,
            feed_url="https://news.google.com/rss/search",
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            retries_used=0,
            cache_hit=False,
            duration_ms=2.5,
            feed_metadata={"feed_title": "Google News"},
            records=(record,),
        )

    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.rss_news_adapter.GoogleNewsRssAdapter.fetch",
        fake_news_fetch,
    )

    backfill_exit = main(
        [
            "backfill-news",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--topic",
            "WORLD",
            "--keyword",
            "inflation",
            "--json",
        ]
    )
    assert backfill_exit == 0
    backfill_payload = json.loads(capsys.readouterr().out)
    assert backfill_payload["corpus_id"] == "it-news-corpus"
    assert backfill_payload["queries_processed"] == 2
    assert backfill_payload["normalized_rows_persisted"] == 2

    inspect_exit = main(
        [
            "inspect-news-corpus",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["news_articles"] == 2

    verify_exit = main(
        [
            "verify-news-source",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--topic",
            "WORLD",
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["source_verification"]["ok"] is True
    assert verify_payload["corpus_verification"]["ok"] is True


def test_strategy_research_cli_reddit_backfill_inspect_verify(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_reddit_ingestion_config(app_cfg)
    monkeypatch.setenv("REDDIT_ACCESS_TOKEN", "test-token")

    def fake_verify_oauth(self: object) -> dict[str, object]:
        del self
        return {
            "username": "it-user",
            "retries_used": 0,
            "duration_ms": 1.0,
            "rate_limit_remaining": 10.0,
            "rate_limit_reset_sec": 1.0,
            "rate_limit_used": 0.0,
        }

    def fake_fetch_submissions(
        self: object,
        query: object,
        *,
        limit: int = 50,
        after: str | None = None,
    ) -> RedditFetchPage:
        del self, limit, after
        query_text = str(getattr(query, "value", "worldnews"))
        query_kind = getattr(query, "kind", RedditQueryKind.SUBREDDIT)
        record = RedditEvidenceRecord(
            source_id="reddit",
            source_class="reddit",
            source_name="reddit_oauth_adapter",
            query=query_text,
            query_kind=query_kind,
            evidence_kind=RedditEvidenceKind.SUBMISSION,
            dedup_key=f"reddit:sha256:sub-{query_text}",
            source_record_id=f"t3-{query_text}",
            post_id=f"post-{query_text}",
            comment_id="",
            parent_post_id=f"post-{query_text}",
            subreddit="worldnews",
            subreddit_id="t5_worldnews",
            title=f"Submission {query_text}",
            body="submission-body",
            author="author1",
            score=12,
            num_comments=1,
            permalink_url=f"https://www.reddit.com/r/worldnews/comments/post-{query_text}/",
            external_url=f"https://example.test/{query_text}",
            created_at_utc=datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            subreddit_metadata={
                "subreddit": "worldnews",
                "subreddit_id": "t5_worldnews",
                "title": "World News",
                "public_description": "desc",
                "subscribers": 1000,
                "url": "/r/worldnews/",
                "over18": False,
            },
            source_metadata={"query_kind": query_kind.value},
            raw_payload={"query": query_text},
        )
        return RedditFetchPage(
            source_id="reddit",
            source_name="reddit_oauth_adapter",
            query=query_text,
            query_kind=query_kind,
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=2.0,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=10.0,
            rate_limit_reset_sec=1.0,
            rate_limit_used=0.0,
            records=(record,),
        )

    def fake_fetch_comments(
        self: object,
        *,
        post_id: str,
        query: object,
        limit: int = 25,
    ) -> RedditFetchPage:
        del self, limit
        query_text = str(getattr(query, "value", "worldnews"))
        query_kind = getattr(query, "kind", RedditQueryKind.SUBREDDIT)
        comment = RedditEvidenceRecord(
            source_id="reddit",
            source_class="reddit",
            source_name="reddit_oauth_adapter",
            query=query_text,
            query_kind=query_kind,
            evidence_kind=RedditEvidenceKind.COMMENT,
            dedup_key=f"reddit:sha256:comment-{post_id}",
            source_record_id=f"t1-comment-{post_id}",
            post_id=post_id,
            comment_id=f"comment-{post_id}",
            parent_post_id=post_id,
            subreddit="worldnews",
            subreddit_id="t5_worldnews",
            title="",
            body="comment-body",
            author="author2",
            score=3,
            num_comments=None,
            permalink_url=f"https://www.reddit.com/r/worldnews/comments/{post_id}/comment/",
            external_url=f"https://www.reddit.com/comments/{post_id}",
            created_at_utc=datetime(2026, 1, 10, 1, 0, tzinfo=UTC),
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            subreddit_metadata={
                "subreddit": "worldnews",
                "subreddit_id": "t5_worldnews",
                "title": "World News",
                "public_description": "desc",
                "subscribers": 1000,
                "url": "/r/worldnews/",
                "over18": False,
            },
            source_metadata={"query_kind": query_kind.value},
            raw_payload={"query": query_text},
        )
        return RedditFetchPage(
            source_id="reddit",
            source_name="reddit_oauth_adapter",
            query=query_text,
            query_kind=query_kind,
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=1.5,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=10.0,
            rate_limit_reset_sec=1.0,
            rate_limit_used=0.0,
            records=(comment,),
        )

    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.reddit_oauth_adapter.RedditOAuthAdapter.verify_oauth",
        fake_verify_oauth,
    )
    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.reddit_oauth_adapter.RedditOAuthAdapter.fetch_submissions",
        fake_fetch_submissions,
    )
    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.reddit_oauth_adapter.RedditOAuthAdapter.fetch_comments",
        fake_fetch_comments,
    )

    backfill_exit = main(
        [
            "backfill-reddit",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--subreddit",
            "worldnews",
            "--include-comments",
            "--json",
        ]
    )
    assert backfill_exit == 0
    backfill_payload = json.loads(capsys.readouterr().out)
    assert backfill_payload["corpus_id"] == "it-reddit-corpus"
    assert backfill_payload["queries_processed"] == 1
    assert backfill_payload["submissions_persisted"] == 1
    assert backfill_payload["comments_persisted"] == 1

    inspect_exit = main(
        [
            "inspect-reddit-corpus",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["reddit_evidence"] == 2

    verify_exit = main(
        [
            "verify-reddit-oauth",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--subreddit",
            "worldnews",
            "--include-comments",
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["source_verification"]["ok"] is True
    assert verify_payload["source_verification"]["oauth_user"] == "it-user"
    assert verify_payload["corpus_verification"]["ok"] is True


def test_strategy_research_cli_build_inspect_verify_linkage(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_news_ingestion_config(app_cfg)
    _set_linkage_config(app_cfg)
    _seed_market_dataset(app_cfg)

    news_root = app_cfg.parent / "strategy-research-news" / "it-news-corpus" / "normalized"
    news_root.mkdir(parents=True, exist_ok=True)
    (news_root / "news_articles.jsonl").write_text(
        json.dumps(
            {
                "source_class": "news_rss_web",
                "source_name": "rss_news_adapter",
                "dedup_key": "news-link-1",
                "source_record_id": "rec-link-1",
                "title": "Analysts debate whether policy A will pass by Q1",
                "summary": "The proposal gains momentum in parliament.",
                "article_url": "https://example.test/policy-a",
                "published_at_utc": "2026-01-10T00:00:00+00:00",
                "fetched_at_utc": "2026-01-10T01:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    build_exit = main(
        [
            "build-linkage",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert build_exit == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["linkage_id"] == "it-linkage"
    assert build_payload["linked_count"] == 1

    inspect_exit = main(
        [
            "inspect-linkage",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["linkage_results"] == 1
    assert inspect_payload["state_counts"]["linked"] == 1

    verify_exit = main(
        [
            "verify-linkage-quality",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True


def test_strategy_research_cli_enrich_alt_data_and_inspect(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_news_ingestion_config(app_cfg)
    _set_reddit_ingestion_config(app_cfg)
    _set_x_ingestion_config(app_cfg)
    _set_linkage_config(app_cfg)
    _set_llm_enrichment_config(app_cfg)
    _seed_alt_data_linkage_inputs(app_cfg)

    enrich_exit = main(
        [
            "enrich-alt-data",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert enrich_exit == 0
    enrich_payload = json.loads(capsys.readouterr().out)
    assert enrich_payload["enrichment_id"] == "it-enrichment"
    assert enrich_payload["records_processed"] == 2
    assert enrich_payload["failures"] == 0

    inspect_exit = main(
        [
            "inspect-llm-enrichment",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["enrichment_records"] == 2
    assert inspect_payload["status_counts"]["enriched"] >= 1


def test_strategy_research_cli_build_inspect_verify_alt_feature_dataset(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_news_ingestion_config(app_cfg)
    _set_reddit_ingestion_config(app_cfg)
    _set_x_ingestion_config(app_cfg)
    _set_linkage_config(app_cfg)
    _set_llm_enrichment_config(app_cfg)
    _seed_market_dataset(app_cfg)
    _seed_alt_data_linkage_inputs(app_cfg)

    markets_path = app_cfg.parent / "strategy-research" / "it-ds" / "normalized" / "markets.jsonl"
    market_rows = [json.loads(line) for line in markets_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    market_rows[0]["decision_timestamp_utc"] = "2026-01-11T00:00:00+00:00"
    markets_path.write_text("\n".join(json.dumps(row) for row in market_rows) + "\n", encoding="utf-8")

    enrich_exit = main(
        [
            "enrich-alt-data",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert enrich_exit == 0
    enrich_payload = json.loads(capsys.readouterr().out)
    assert enrich_payload["records_processed"] == 2

    build_exit = main(
        [
            "build-alt-feature-dataset",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--linkage-id",
            "it-linkage",
            "--news-corpus-id",
            "it-news-corpus",
            "--reddit-corpus-id",
            "it-reddit-corpus",
            "--x-corpus-id",
            "it-x-corpus",
            "--enrichment-id",
            "it-enrichment",
            "--json",
        ]
    )
    assert build_exit == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["rows_written"] == 1
    assert build_payload["schema_version"] == "alt-v1"

    inspect_exit = main(
        [
            "inspect-alt-feature-schema",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["schema_exists"] is True
    assert inspect_payload["rows_count"] == 1
    assert any(col.get("name") == "f_alt_news_volume_24h" for col in inspect_payload["columns"])

    verify_exit = main(
        [
            "verify-alt-feature-parity",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True


def test_strategy_research_cli_x_backfill_inspect_verify(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _set_x_ingestion_config(app_cfg)
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")

    def fake_fetch_search(
        self: object,
        query: object,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> XFetchPage:
        del self, limit, cursor
        query_text = str(getattr(query, "value", "macro"))
        query_kind = getattr(query, "kind", XQueryKind.KEYWORD)
        record = XPostRecord(
            source_id="x",
            source_class="x",
            source_name="x_api_adapter",
            query=query_text,
            query_kind=query_kind,
            dedup_key=f"x:sha256:search-{query_text}",
            source_record_id=f"post-search-{query_text}",
            post_id=f"post-search-{query_text}",
            post_url=f"https://x.com/i/web/status/post-search-{query_text}",
            author_id="u-search",
            author_username="",
            text=f"Search post {query_text}",
            lang="en",
            conversation_id=f"post-search-{query_text}",
            public_metrics={"like_count": 2},
            created_at_utc=datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            source_metadata={"query_kind": query_kind.value, "auth_mode": "user_context"},
            raw_payload={"query": query_text},
        )
        return XFetchPage(
            source_id="x",
            source_name="x_api_adapter",
            query=query_text,
            query_kind=query_kind,
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=2.0,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=10.0,
            rate_limit_reset_epoch=1767350500.0,
            records=(record,),
        )

    def fake_fetch_account_posts(
        self: object,
        query: object,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> XFetchPage:
        del self, limit, cursor
        query_text = str(getattr(query, "value", "analyst"))
        query_kind = getattr(query, "kind", XQueryKind.ACCOUNT)
        record = XPostRecord(
            source_id="x",
            source_class="x",
            source_name="x_api_adapter",
            query=query_text,
            query_kind=query_kind,
            dedup_key=f"x:sha256:account-{query_text}",
            source_record_id=f"post-account-{query_text}",
            post_id=f"post-account-{query_text}",
            post_url=f"https://x.com/{query_text}/status/post-account-{query_text}",
            author_id="u-account",
            author_username=query_text,
            text=f"Account post {query_text}",
            lang="en",
            conversation_id=f"post-account-{query_text}",
            public_metrics={"like_count": 3},
            created_at_utc=datetime(2026, 1, 10, 1, 0, tzinfo=UTC),
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            source_metadata={"query_kind": query_kind.value, "auth_mode": "user_context"},
            raw_payload={"query": query_text},
        )
        return XFetchPage(
            source_id="x",
            source_name="x_api_adapter",
            query=query_text,
            query_kind=query_kind,
            fetched_at_utc=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=1.5,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=10.0,
            rate_limit_reset_epoch=1767350500.0,
            records=(record,),
        )

    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.x_api_adapter.XApiAdapter.fetch_search",
        fake_fetch_search,
    )
    monkeypatch.setattr(
        "prediction_market_bot.infrastructure.alt_data.adapters.x_api_adapter.XApiAdapter.fetch_account_posts",
        fake_fetch_account_posts,
    )

    backfill_exit = main(
        [
            "backfill-x",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--account",
            "analyst",
            "--keyword",
            "macro",
            "--json",
        ]
    )
    assert backfill_exit == 0
    backfill_payload = json.loads(capsys.readouterr().out)
    assert backfill_payload["corpus_id"] == "it-x-corpus"
    assert backfill_payload["queries_processed"] == 2
    assert backfill_payload["normalized_rows_persisted"] == 2

    inspect_exit = main(
        [
            "inspect-x-corpus",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["normalized_counts"]["x_evidence"] == 2

    verify_exit = main(
        [
            "verify-x-source",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--account",
            "analyst",
            "--keyword",
            "macro",
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["source_verification"]["ok"] is True
    assert verify_payload["corpus_verification"]["ok"] is True


def test_strategy_research_cli_build_labels_run_compare_benchmarks(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_benchmark_dataset(app_cfg)

    corpus_root = app_cfg.parent / "strategy-research-corpus" / "it-corpus" / "normalized"
    corpus_root.mkdir(parents=True, exist_ok=True)
    corpus_rows = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        sentiment = 0.25 if index % 2 == 0 else -0.25
        corpus_rows.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "decision_timestamp_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "source_type": "RSS",
                "sentiment": sentiment,
                "credibility": 0.75,
                "is_time_aligned": True,
            }
        )
    (corpus_root / "evidence_findings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus_rows) + "\n",
        encoding="utf-8",
    )

    build_exit = main(
        [
            "build-labels",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--corpus-id",
            "it-corpus",
            "--json",
        ]
    )
    assert build_exit == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["labels_written"] == 8

    run_a_exit = main(
        [
            "run-benchmarks",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--split-mode",
            "holdout",
            "--json",
        ]
    )
    assert run_a_exit == 0
    run_a_payload = json.loads(capsys.readouterr().out)
    run_a = run_a_payload["run_id"]
    assert "test" in run_a_payload["aggregate"]

    run_b_exit = main(
        [
            "run-benchmarks",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--split-mode",
            "holdout",
            "--min-confidence",
            "0.8",
            "--json",
        ]
    )
    assert run_b_exit == 0
    run_b_payload = json.loads(capsys.readouterr().out)
    run_b = run_b_payload["run_id"]

    compare_exit = main(
        [
            "compare-benchmarks",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-a",
            run_a,
            "--run-b",
            run_b,
            "--split",
            "test",
            "--json",
        ]
    )
    assert compare_exit == 0
    compare_payload = json.loads(capsys.readouterr().out)
    assert compare_payload["run_a"] == run_a
    assert compare_payload["run_b"] == run_b
    assert "market_implied" in compare_payload["baseline_deltas"]


def test_strategy_research_cli_run_ablation_and_compare_variants(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_benchmark_dataset(app_cfg)

    corpus_root = app_cfg.parent / "strategy-research-corpus" / "it-corpus" / "normalized"
    corpus_root.mkdir(parents=True, exist_ok=True)
    corpus_rows = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        sentiment = 0.20 if index % 2 == 0 else -0.20
        corpus_rows.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "decision_timestamp_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "source_type": "RSS",
                "sentiment": sentiment,
                "credibility": 0.80,
                "is_time_aligned": True,
            }
        )
    (corpus_root / "evidence_findings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus_rows) + "\n",
        encoding="utf-8",
    )

    build_labels_exit = main(
        [
            "build-labels",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--corpus-id",
            "it-corpus",
            "--json",
        ]
    )
    assert build_labels_exit == 0
    labels_payload = json.loads(capsys.readouterr().out)
    assert labels_payload["labels_written"] == 8

    labels_path = app_cfg.parent / "strategy-research" / "it-ds" / "derived" / "labels.jsonl"
    label_rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    alt_rows = []
    for row in label_rows:
        favorable = int(row.get("label_yes", 0)) == 1
        alt_rows.append(
            {
                "row_id": row["row_id"],
                "f_alt_news_volume_24h": 5 if favorable else 2,
                "f_alt_news_burstiness_24h": 0.8 if favorable else -0.2,
                "f_alt_source_diversity": 0.72 if favorable else 0.33,
                "f_alt_freshness_decay": 0.81 if favorable else 0.42,
                "f_alt_source_credibility_prior": 0.70,
                "f_alt_reddit_mentions_24h": 4 if favorable else 1,
                "f_alt_reddit_attention": 1.3 if favorable else 0.2,
                "f_alt_x_mentions_24h": 3 if favorable else 1,
                "f_alt_x_attention": 1.1 if favorable else 0.1,
                "f_alt_market_linked_coverage": 0.88 if favorable else 0.61,
                "f_alt_contradiction_score": 0.14 if favorable else 0.44,
                "f_alt_novelty_score": 0.66 if favorable else 0.24,
                "f_alt_catalyst_strength_score": 0.76 if favorable else 0.36,
                "f_alt_enrichment_coverage": 0.90 if favorable else 0.58,
            }
        )
    alt_rows_path = app_cfg.parent / "strategy-research" / "it-ds" / "derived" / "alt_feature_store" / "alt-v1" / "alt_feature_rows.jsonl"
    alt_rows_path.parent.mkdir(parents=True, exist_ok=True)
    alt_rows_path.write_text("\n".join(json.dumps(row) for row in alt_rows) + "\n", encoding="utf-8")

    ablation_exit = main(
        [
            "run-ablation-study",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--split-mode",
            "walk-forward",
            "--train-days",
            "4",
            "--validation-days",
            "2",
            "--test-days",
            "2",
            "--step-days",
            "2",
            "--json",
        ]
    )
    assert ablation_exit == 0
    ablation_payload = json.loads(capsys.readouterr().out)
    assert ablation_payload["run_id"].startswith("ablation-")
    assert "test" in ablation_payload["aggregate"]
    assert "market_plus_alt_data_with_llm_enrichment" in ablation_payload["aggregate"]["test"]

    compare_exit = main(
        [
            "compare-alt-data-variants",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-id",
            str(ablation_payload["run_id"]),
            "--split",
            "test",
            "--reference-variant",
            "market_only_baseline",
            "--json",
        ]
    )
    assert compare_exit == 0
    compare_payload = json.loads(capsys.readouterr().out)
    assert compare_payload["run_id"] == ablation_payload["run_id"]
    assert "market_plus_news" in compare_payload["variant_deltas"]
    assert Path(compare_payload["report_path"]).exists()


def test_strategy_research_cli_run_walk_forward_and_generate_report(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_benchmark_dataset(app_cfg)

    corpus_root = app_cfg.parent / "strategy-research-corpus" / "it-corpus" / "normalized"
    corpus_root.mkdir(parents=True, exist_ok=True)
    corpus_rows = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        sentiment = 0.2 if index % 2 == 0 else -0.2
        corpus_rows.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "decision_timestamp_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "source_type": "RSS",
                "sentiment": sentiment,
                "credibility": 0.8,
                "is_time_aligned": True,
            }
        )
    (corpus_root / "evidence_findings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus_rows) + "\n",
        encoding="utf-8",
    )

    build_exit = main(
        [
            "build-labels",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--corpus-id",
            "it-corpus",
            "--json",
        ]
    )
    assert build_exit == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["labels_written"] == 8

    walk_exit = main(
        [
            "run-walk-forward",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--baseline-name",
            "heuristic_prediction_agent",
            "--eval-split",
            "test",
            "--train-days",
            "4",
            "--validation-days",
            "2",
            "--test-days",
            "2",
            "--step-days",
            "2",
            "--json",
        ]
    )
    assert walk_exit == 0
    walk_payload = json.loads(capsys.readouterr().out)
    assert walk_payload["run_id"].startswith("walk-")
    assert walk_payload["aggregate_metrics"]["total_predictions"] > 0
    assert len(walk_payload["folds"]) >= 1

    report_exit = main(
        [
            "generate-strategy-report",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-id",
            walk_payload["run_id"],
            "--json",
        ]
    )
    assert report_exit == 0
    report_payload = json.loads(capsys.readouterr().out)
    report_path = Path(report_payload["report_path"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "Strategy Walk-Forward Report" in report_text


def test_strategy_research_cli_build_inspect_verify_feature_dataset(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_benchmark_dataset(app_cfg)

    corpus_root = app_cfg.parent / "strategy-research-corpus" / "it-corpus" / "normalized"
    corpus_root.mkdir(parents=True, exist_ok=True)
    corpus_rows = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        corpus_rows.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "decision_timestamp_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "source_name": "wikipedia-search",
                "source_type": "RSS",
                "sentiment": 0.2 if index % 2 == 0 else -0.2,
                "credibility": 0.75,
                "published_at_utc": f"2025-12-{20+index:02d}T00:00:00+00:00",
            }
        )
    (corpus_root / "evidence_findings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus_rows) + "\n",
        encoding="utf-8",
    )

    build_exit = main(
        [
            "build-feature-dataset",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--corpus-id",
            "it-corpus",
            "--json",
        ]
    )
    assert build_exit == 0
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["rows_written"] == 8
    assert build_payload["schema_version"] == "v1"

    inspect_exit = main(
        [
            "inspect-feature-schema",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )
    assert inspect_exit == 0
    inspect_payload = json.loads(capsys.readouterr().out)
    assert inspect_payload["schema_exists"] is True
    assert inspect_payload["rows_count"] == 8
    assert any(col.get("name") == "f_market_yes_price" for col in inspect_payload["columns"])

    verify_exit = main(
        [
            "verify-feature-parity",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )
    assert verify_exit == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True


def test_strategy_research_cli_train_calibrate_compare_and_model_card(
    temp_config_paths: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _set_strategy_research_endpoints(app_cfg)
    _seed_benchmark_dataset(app_cfg)

    corpus_root = app_cfg.parent / "strategy-research-corpus" / "it-corpus" / "normalized"
    corpus_root.mkdir(parents=True, exist_ok=True)
    corpus_rows = []
    for index in range(8):
        market_id = f"bm-{index+1}"
        decision_day = 1 + index
        corpus_rows.append(
            {
                "market_id": market_id,
                "event_id": f"be-{index+1}",
                "decision_timestamp_utc": f"2026-01-{decision_day:02d}T00:00:00+00:00",
                "source_name": "wikipedia-search",
                "source_type": "RSS",
                "sentiment": 0.25 if index % 2 == 0 else -0.25,
                "credibility": 0.8,
                "published_at_utc": f"2025-12-{20+index:02d}T00:00:00+00:00",
            }
        )
    (corpus_root / "evidence_findings.jsonl").write_text(
        "\n".join(json.dumps(row) for row in corpus_rows) + "\n",
        encoding="utf-8",
    )

    build_feature_exit = main(
        [
            "build-feature-dataset",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--corpus-id",
            "it-corpus",
            "--json",
        ]
    )
    assert build_feature_exit == 0
    feature_payload = json.loads(capsys.readouterr().out)
    assert feature_payload["rows_written"] == 8

    train_exit = main(
        [
            "train-baseline-models",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--no-xgboost",
            "--json",
        ]
    )
    assert train_exit == 0
    train_payload = json.loads(capsys.readouterr().out)
    train_run_id = str(train_payload["run_id"])
    assert train_run_id.startswith("train-")
    assert "logistic_regression_baseline" in train_payload["models"]
    assert "tree_baseline" in train_payload["models"]

    compare_exit = main(
        [
            "compare-models",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-id",
            train_run_id,
            "--split",
            "test",
            "--json",
        ]
    )
    assert compare_exit == 0
    compare_payload = json.loads(capsys.readouterr().out)
    assert compare_payload["run_id"] == train_run_id
    assert len(compare_payload["ranking"]) >= 1

    calibrate_exit = main(
        [
            "calibrate-model",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-id",
            train_run_id,
            "--model-name",
            "logistic_regression_baseline",
            "--method",
            "platt",
            "--fit-split",
            "validation",
            "--eval-split",
            "test",
            "--json",
        ]
    )
    assert calibrate_exit == 0
    calibrate_payload = json.loads(capsys.readouterr().out)
    calibration_run_id = str(calibrate_payload["run_id"])
    assert calibration_run_id.startswith("cal-")

    model_card_exit = main(
        [
            "generate-model-card",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--dataset-id",
            "it-ds",
            "--run-id",
            train_run_id,
            "--model-name",
            "logistic_regression_baseline",
            "--calibration-run-id",
            calibration_run_id,
            "--json",
        ]
    )
    assert model_card_exit == 0
    model_card_payload = json.loads(capsys.readouterr().out)
    card_path = Path(model_card_payload["output_path"])
    assert card_path.exists()
    card_text = card_path.read_text(encoding="utf-8")
    assert "Model Card" in card_text
