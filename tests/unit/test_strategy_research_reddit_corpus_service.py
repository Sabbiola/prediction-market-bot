from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import (
    RedditEvidenceKind,
    RedditEvidenceRecord,
    RedditFetchPage,
    RedditQuery,
)
from prediction_market_bot.infrastructure.alt_data.adapters.reddit_oauth_adapter import RedditSourceFetchError
from prediction_market_bot.strategy_research.reddit_corpus import RedditCorpusService


def _submission(*, query: RedditQuery, dedup_key: str, post_id: str, idx: int) -> RedditEvidenceRecord:
    return RedditEvidenceRecord(
        source_id="reddit",
        source_class="reddit",
        source_name="reddit_oauth_adapter",
        query=query.value,
        query_kind=query.kind,
        evidence_kind=RedditEvidenceKind.SUBMISSION,
        dedup_key=dedup_key,
        source_record_id=f"t3_{post_id}",
        post_id=post_id,
        comment_id="",
        parent_post_id=post_id,
        subreddit="worldnews",
        subreddit_id="t5_worldnews",
        title=f"title-{idx}",
        body=f"body-{idx}",
        author="alice",
        score=10 + idx,
        num_comments=2,
        permalink_url=f"https://www.reddit.com/r/worldnews/comments/{post_id}/title-{idx}/",
        external_url=f"https://example.test/{idx}",
        created_at_utc=datetime(2026, 1, idx, 10, 0, tzinfo=UTC),
        fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        subreddit_metadata={
            "subreddit": "worldnews",
            "subreddit_id": "t5_worldnews",
            "title": "World News",
            "public_description": "desc",
            "subscribers": 1000,
            "url": "/r/worldnews/",
            "over18": False,
            "fetched_at_utc": "2026-02-01T10:00:00+00:00",
        },
        source_metadata={"query_kind": query.kind.value},
        raw_payload={"id": post_id},
    )


def _comment(*, query: RedditQuery, dedup_key: str, post_id: str, comment_id: str, idx: int) -> RedditEvidenceRecord:
    return RedditEvidenceRecord(
        source_id="reddit",
        source_class="reddit",
        source_name="reddit_oauth_adapter",
        query=query.value,
        query_kind=query.kind,
        evidence_kind=RedditEvidenceKind.COMMENT,
        dedup_key=dedup_key,
        source_record_id=f"t1_{comment_id}",
        post_id=post_id,
        comment_id=comment_id,
        parent_post_id=post_id,
        subreddit="worldnews",
        subreddit_id="t5_worldnews",
        title="",
        body=f"comment-{idx}",
        author="bob",
        score=3,
        num_comments=None,
        permalink_url=f"https://www.reddit.com/r/worldnews/comments/{post_id}/title/{comment_id}/",
        external_url=f"https://www.reddit.com/comments/{comment_id}",
        created_at_utc=datetime(2026, 1, idx, 11, 0, tzinfo=UTC),
        fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
        subreddit_metadata={
            "subreddit": "worldnews",
            "subreddit_id": "t5_worldnews",
            "title": "World News",
            "public_description": "desc",
            "subscribers": 1000,
            "url": "/r/worldnews/",
            "over18": False,
            "fetched_at_utc": "2026-02-01T10:00:00+00:00",
        },
        source_metadata={"query_kind": query.kind.value},
        raw_payload={"id": comment_id},
    )


class _FakeRedditAdapter:
    def __init__(self) -> None:
        self.submission_calls: list[tuple[str, str | None]] = []
        self.comment_calls: list[str] = []

    def verify_oauth(self) -> dict[str, object]:
        return {
            "username": "tester",
            "retries_used": 0,
            "duration_ms": 1.0,
            "rate_limit_remaining": 10.0,
            "rate_limit_reset_sec": 1.0,
            "rate_limit_used": 0.0,
        }

    def fetch_submissions(self, query: RedditQuery, *, limit: int = 50, after: str | None = None) -> RedditFetchPage:
        del limit
        self.submission_calls.append((query.key, after))
        if query.value == "broken":
            raise RedditSourceFetchError(source_id="reddit", reason_code="fetch_failed", detail="TimeoutError")
        if after is None:
            records = (_submission(query=query, dedup_key="dedup-sub-1", post_id="abc1", idx=1),)
            next_cursor = "cursor-1"
        else:
            records = (_submission(query=query, dedup_key="dedup-sub-2", post_id="abc2", idx=2),)
            next_cursor = None
        return RedditFetchPage(
            source_id="reddit",
            source_name="reddit_oauth_adapter",
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            retries_used=1,
            duration_ms=2.5,
            cache_hit=False,
            next_cursor=next_cursor,
            rate_limit_remaining=10.0,
            rate_limit_reset_sec=1.0,
            rate_limit_used=0.0,
            records=records,
        )

    def fetch_comments(self, *, post_id: str, query: RedditQuery, limit: int = 25) -> RedditFetchPage:
        del limit
        self.comment_calls.append(post_id)
        record = _comment(
            query=query,
            dedup_key=f"dedup-com-{post_id}",
            post_id=post_id,
            comment_id=f"c-{post_id}",
            idx=3,
        )
        return RedditFetchPage(
            source_id="reddit",
            source_name="reddit_oauth_adapter",
            query=query.value,
            query_kind=query.kind,
            fetched_at_utc=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            retries_used=0,
            duration_ms=1.5,
            cache_hit=False,
            next_cursor=None,
            rate_limit_remaining=10.0,
            rate_limit_reset_sec=1.0,
            rate_limit_used=0.0,
            records=(record,),
        )


def test_reddit_corpus_backfill_checkpoint_and_comments(tmp_path: Path) -> None:
    adapter = _FakeRedditAdapter()
    service = RedditCorpusService(
        base_dir=tmp_path / "reddit-corpus",
        default_corpus_id="reddit-it",
        source_id="reddit",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        default_max_pages_per_query=1,
        default_include_comments=True,
        throttle_sec=0.0,
    )

    first = service.backfill_reddit(subreddits=("worldnews",))
    assert first.query_count == 1
    assert first.queries_processed == 1
    assert first.pages_fetched == 1
    assert first.checkpoint_completed is False
    assert first.submissions_persisted == 1
    assert first.comments_persisted == 1
    assert first.subreddit_rows_persisted == 1

    second = service.backfill_reddit(subreddits=("worldnews",))
    assert second.pages_fetched == 1
    assert second.checkpoint_completed is True
    assert second.submissions_persisted == 1
    assert second.comments_persisted == 1
    assert adapter.submission_calls == [("subreddit:worldnews", None), ("subreddit:worldnews", "cursor-1")]

    inspection = service.inspect_corpus()
    assert inspection.normalized_counts["reddit_evidence"] == 4
    assert inspection.normalized_counts["reddit_subreddits"] == 1

    verification = service.verify_corpus()
    assert verification.ok is True


def test_reddit_corpus_backfill_tracks_source_failures(tmp_path: Path) -> None:
    adapter = _FakeRedditAdapter()
    service = RedditCorpusService(
        base_dir=tmp_path / "reddit-corpus",
        default_corpus_id="reddit-it",
        source_id="reddit",
        adapter=adapter,  # type: ignore[arg-type]
        default_limit_per_query=10,
        default_max_pages_per_query=1,
        default_include_comments=False,
        throttle_sec=0.0,
    )

    summary = service.backfill_reddit(subreddits=("broken",))
    assert summary.query_count == 1
    assert summary.queries_processed == 0
    assert summary.source_failures == 1
    assert any("fetch_failed" in warning for warning in summary.warnings)
