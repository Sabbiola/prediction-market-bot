from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import (
    RedditEvidenceKind,
    RedditOAuthAdapter,
    RedditQuery,
    RedditQueryKind,
    RedditSourceFetchError,
)
from prediction_market_bot.infrastructure.alt_data.reddit_models import parse_datetime_utc

from .models import (
    RedditBackfillSummary,
    RedditCorpusInspection,
    RedditCorpusVerification,
    RedditSourceVerification,
    parse_reddit_query_values,
)
from .storage import NORMALIZED_FILES, RAW_FILES, RedditCorpusLayout, RedditCorpusStorage

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class _Counters:
    query_count: int = 0
    queries_processed: int = 0
    queries_skipped: int = 0
    pages_fetched: int = 0
    source_failures: int = 0
    retries_used: int = 0
    raw_rows_persisted: int = 0
    normalized_rows_persisted: int = 0
    normalized_rows_deduped: int = 0
    submissions_persisted: int = 0
    comments_persisted: int = 0
    subreddit_rows_persisted: int = 0


class RedditCorpusService:
    def __init__(
        self,
        *,
        base_dir: Path,
        default_corpus_id: str,
        source_id: str,
        adapter: RedditOAuthAdapter,
        default_limit_per_query: int = 50,
        default_comment_limit_per_post: int = 25,
        default_max_pages_per_query: int = 5,
        default_include_comments: bool = False,
        throttle_sec: float = 0.0,
    ) -> None:
        self.base_dir = base_dir
        self.default_corpus_id = default_corpus_id
        self.source_id = source_id.strip().lower()
        self.adapter = adapter
        self.default_limit_per_query = max(default_limit_per_query, 1)
        self.default_comment_limit_per_post = max(default_comment_limit_per_post, 1)
        self.default_max_pages_per_query = max(default_max_pages_per_query, 0)
        self.default_include_comments = bool(default_include_comments)
        self.throttle_sec = max(throttle_sec, 0.0)

    def backfill_reddit(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        subreddits: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
        max_pages_per_query: int | None = None,
        include_comments: bool | None = None,
        comment_limit_per_post: int | None = None,
        incremental: bool = False,
    ) -> RedditBackfillSummary:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        queries = parse_reddit_query_values(subreddits=subreddits, keywords=keywords)
        if not queries:
            raise ValueError("At least one query is required. Use --subreddit and/or --keyword.")

        layout = RedditCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = RedditCorpusStorage(layout)
        if reset_checkpoint:
            storage.clear_checkpoint()

        checkpoint = storage.load_checkpoint(corpus_id=effective_corpus_id, source_id=self.source_id)
        counters = _Counters(query_count=len(queries))
        warnings: list[str] = []
        started_at_utc = datetime.now(UTC)
        effective_limit = max(limit_per_query if limit_per_query is not None else self.default_limit_per_query, 1)
        effective_max_pages = max(
            max_pages_per_query if max_pages_per_query is not None else self.default_max_pages_per_query,
            0,
        )
        effective_include_comments = (
            bool(include_comments) if include_comments is not None else self.default_include_comments
        )
        effective_comment_limit = max(
            comment_limit_per_post if comment_limit_per_post is not None else self.default_comment_limit_per_post,
            1,
        )

        existing_raw_keys = self._load_existing_raw_keys(storage)
        existing_normalized_keys = self._load_existing_normalized_keys(storage)
        existing_subreddit_keys = self._load_existing_subreddit_keys(storage)
        query_keys = {query.key for query in queries}

        for query_index, query in enumerate(queries):
            if not incremental and query.key in checkpoint.completed_query_keys:
                counters = _Counters(
                    query_count=counters.query_count,
                    queries_processed=counters.queries_processed,
                    queries_skipped=counters.queries_skipped + 1,
                    pages_fetched=counters.pages_fetched,
                    source_failures=counters.source_failures,
                    retries_used=counters.retries_used,
                    raw_rows_persisted=counters.raw_rows_persisted,
                    normalized_rows_persisted=counters.normalized_rows_persisted,
                    normalized_rows_deduped=counters.normalized_rows_deduped,
                    submissions_persisted=counters.submissions_persisted,
                    comments_persisted=counters.comments_persisted,
                    subreddit_rows_persisted=counters.subreddit_rows_persisted,
                )
                continue
            if self.throttle_sec > 0 and query_index > 0:
                time.sleep(self.throttle_sec)

            after_cursor = None if incremental else checkpoint.query_cursors.get(query.key)
            pages_for_query = 0
            query_failed = False
            while True:
                if self.throttle_sec > 0 and pages_for_query > 0:
                    time.sleep(self.throttle_sec)
                try:
                    page = self.adapter.fetch_submissions(query, limit=effective_limit, after=after_cursor)
                except RedditSourceFetchError as exc:
                    query_failed = True
                    counters = _Counters(
                        query_count=counters.query_count,
                        queries_processed=counters.queries_processed,
                        queries_skipped=counters.queries_skipped,
                        pages_fetched=counters.pages_fetched,
                        source_failures=counters.source_failures + 1,
                        retries_used=counters.retries_used,
                        raw_rows_persisted=counters.raw_rows_persisted,
                        normalized_rows_persisted=counters.normalized_rows_persisted,
                        normalized_rows_deduped=counters.normalized_rows_deduped,
                        submissions_persisted=counters.submissions_persisted,
                        comments_persisted=counters.comments_persisted,
                        subreddit_rows_persisted=counters.subreddit_rows_persisted,
                    )
                    warnings.append(f"query={query.key} fetch_failed reason={exc.reason_code} detail={exc.detail}")
                    break

                pages_for_query += 1
                counters = _Counters(
                    query_count=counters.query_count,
                    queries_processed=counters.queries_processed,
                    queries_skipped=counters.queries_skipped,
                    pages_fetched=counters.pages_fetched + 1,
                    source_failures=counters.source_failures,
                    retries_used=counters.retries_used + page.retries_used,
                    raw_rows_persisted=counters.raw_rows_persisted,
                    normalized_rows_persisted=counters.normalized_rows_persisted,
                    normalized_rows_deduped=counters.normalized_rows_deduped,
                    submissions_persisted=counters.submissions_persisted,
                    comments_persisted=counters.comments_persisted,
                    subreddit_rows_persisted=counters.subreddit_rows_persisted,
                )
                counters = self._persist_page_records(
                    counters=counters,
                    storage=storage,
                    query=query,
                    page_records=page.records,
                    existing_raw_keys=existing_raw_keys,
                    existing_normalized_keys=existing_normalized_keys,
                )
                counters = self._persist_subreddit_metadata_rows(
                    counters=counters,
                    storage=storage,
                    page_records=page.records,
                    existing_subreddit_keys=existing_subreddit_keys,
                )
                if effective_include_comments:
                    for record in page.records:
                        if record.evidence_kind != RedditEvidenceKind.SUBMISSION:
                            continue
                        if not record.post_id:
                            continue
                        try:
                            comments_page = self.adapter.fetch_comments(
                                post_id=record.post_id,
                                query=RedditQuery(value=query.value, kind=query.kind),
                                limit=effective_comment_limit,
                            )
                        except RedditSourceFetchError as exc:
                            warnings.append(
                                f"post_id={record.post_id} comments_fetch_failed reason={exc.reason_code} detail={exc.detail}"
                            )
                            counters = _Counters(
                                query_count=counters.query_count,
                                queries_processed=counters.queries_processed,
                                queries_skipped=counters.queries_skipped,
                                pages_fetched=counters.pages_fetched,
                                source_failures=counters.source_failures + 1,
                                retries_used=counters.retries_used,
                                raw_rows_persisted=counters.raw_rows_persisted,
                                normalized_rows_persisted=counters.normalized_rows_persisted,
                                normalized_rows_deduped=counters.normalized_rows_deduped,
                                submissions_persisted=counters.submissions_persisted,
                                comments_persisted=counters.comments_persisted,
                                subreddit_rows_persisted=counters.subreddit_rows_persisted,
                            )
                            continue
                        counters = _Counters(
                            query_count=counters.query_count,
                            queries_processed=counters.queries_processed,
                            queries_skipped=counters.queries_skipped,
                            pages_fetched=counters.pages_fetched,
                            source_failures=counters.source_failures,
                            retries_used=counters.retries_used + comments_page.retries_used,
                            raw_rows_persisted=counters.raw_rows_persisted,
                            normalized_rows_persisted=counters.normalized_rows_persisted,
                            normalized_rows_deduped=counters.normalized_rows_deduped,
                            submissions_persisted=counters.submissions_persisted,
                            comments_persisted=counters.comments_persisted,
                            subreddit_rows_persisted=counters.subreddit_rows_persisted,
                        )
                        counters = self._persist_page_records(
                            counters=counters,
                            storage=storage,
                            query=query,
                            page_records=comments_page.records,
                            existing_raw_keys=existing_raw_keys,
                            existing_normalized_keys=existing_normalized_keys,
                        )
                        counters = self._persist_subreddit_metadata_rows(
                            counters=counters,
                            storage=storage,
                            page_records=comments_page.records,
                            existing_subreddit_keys=existing_subreddit_keys,
                        )

                after_cursor = page.next_cursor
                if after_cursor:
                    checkpoint.query_cursors[query.key] = after_cursor
                else:
                    checkpoint.query_cursors.pop(query.key, None)
                    if not incremental:
                        checkpoint.completed_query_keys.add(query.key)
                checkpoint.updated_at_utc = datetime.now(UTC)
                storage.save_checkpoint(checkpoint)

                if not after_cursor:
                    break
                if incremental:
                    break
                if effective_max_pages > 0 and pages_for_query >= effective_max_pages:
                    break

            if query_failed:
                continue
            counters = _Counters(
                query_count=counters.query_count,
                queries_processed=counters.queries_processed + 1,
                queries_skipped=counters.queries_skipped,
                pages_fetched=counters.pages_fetched,
                source_failures=counters.source_failures,
                retries_used=counters.retries_used,
                raw_rows_persisted=counters.raw_rows_persisted,
                normalized_rows_persisted=counters.normalized_rows_persisted,
                normalized_rows_deduped=counters.normalized_rows_deduped,
                submissions_persisted=counters.submissions_persisted,
                comments_persisted=counters.comments_persisted,
                subreddit_rows_persisted=counters.subreddit_rows_persisted,
            )

        if incremental:
            checkpoint.last_incremental_at_utc = datetime.now(UTC)
            checkpoint.completed_at_utc = None
        elif query_keys.issubset(checkpoint.completed_query_keys):
            checkpoint.completed_at_utc = datetime.now(UTC)
        else:
            checkpoint.completed_at_utc = None
        checkpoint.updated_at_utc = datetime.now(UTC)
        storage.save_checkpoint(checkpoint)

        finished_at_utc = datetime.now(UTC)
        manifest = {
            "corpus_id": effective_corpus_id,
            "source_id": self.source_id,
            "updated_at_utc": finished_at_utc.isoformat(),
            "incremental": bool(incremental),
            "raw_counts": {name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            "normalized_counts": {name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
            "checkpoint_path": str(layout.checkpoint_path),
            "checkpoint_completed": checkpoint.completed_at_utc is not None,
            "processed_query_count": len(checkpoint.completed_query_keys),
        }
        storage.write_manifest(manifest)
        logger.info(
            "reddit_corpus_backfill_completed",
            extra={
                "event": "reddit_corpus_backfill_completed",
                "corpus_id": effective_corpus_id,
                "source_id": self.source_id,
                "incremental": bool(incremental),
                "query_count": counters.query_count,
                "queries_processed": counters.queries_processed,
                "queries_skipped": counters.queries_skipped,
                "pages_fetched": counters.pages_fetched,
                "source_failures": counters.source_failures,
                "retries_used": counters.retries_used,
                "raw_rows_persisted": counters.raw_rows_persisted,
                "normalized_rows_persisted": counters.normalized_rows_persisted,
                "normalized_rows_deduped": counters.normalized_rows_deduped,
                "submissions_persisted": counters.submissions_persisted,
                "comments_persisted": counters.comments_persisted,
                "subreddit_rows_persisted": counters.subreddit_rows_persisted,
            },
        )
        return RedditBackfillSummary(
            corpus_id=effective_corpus_id,
            source_id=self.source_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            incremental=bool(incremental),
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            query_count=counters.query_count,
            queries_processed=counters.queries_processed,
            queries_skipped=counters.queries_skipped,
            pages_fetched=counters.pages_fetched,
            source_failures=counters.source_failures,
            retries_used=counters.retries_used,
            raw_rows_persisted=counters.raw_rows_persisted,
            normalized_rows_persisted=counters.normalized_rows_persisted,
            normalized_rows_deduped=counters.normalized_rows_deduped,
            submissions_persisted=counters.submissions_persisted,
            comments_persisted=counters.comments_persisted,
            subreddit_rows_persisted=counters.subreddit_rows_persisted,
            warnings=tuple(warnings),
        )

    def inspect_corpus(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> RedditCorpusInspection:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        layout = RedditCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = RedditCorpusStorage(layout)
        checkpoint = storage.load_checkpoint(corpus_id=effective_corpus_id, source_id=self.source_id)
        return RedditCorpusInspection(
            corpus_id=effective_corpus_id,
            source_id=self.source_id,
            corpus_root=layout.root_dir,
            checkpoint_path=layout.checkpoint_path,
            checkpoint_present=layout.checkpoint_path.exists(),
            checkpoint_completed=checkpoint.completed_at_utc is not None,
            processed_query_count=len(checkpoint.completed_query_keys),
            raw_counts={name: storage.row_count(normalized=False, name=name) for name in RAW_FILES},
            normalized_counts={name: storage.row_count(normalized=True, name=name) for name in NORMALIZED_FILES},
        )

    def verify_corpus(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> RedditCorpusVerification:
        inspection = self.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
        layout = RedditCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=inspection.corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = RedditCorpusStorage(layout)
        evidence_rows = storage.read_rows(normalized=True, name="reddit_evidence")

        errors: list[str] = []
        warnings: list[str] = []
        if not evidence_rows:
            warnings.append("normalized/reddit_evidence.jsonl is empty")

        seen_dedup_keys: set[str] = set()
        duplicate_rows = 0
        for row in evidence_rows:
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or "").strip()
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            evidence_kind = str(row.get("evidence_kind") or "").strip()
            subreddit = str(row.get("subreddit") or "").strip()
            permalink = str(row.get("permalink_url") or "").strip()
            created_at = row.get("created_at_utc")
            fetched_at = str(row.get("fetched_at_utc") or "").strip()
            if not dedup_key:
                errors.append("missing dedup_key")
            if not source_record_id:
                errors.append("missing source_record_id")
            if not query:
                errors.append("missing query")
            if query_kind not in {"subreddit", "keyword"}:
                errors.append(f"invalid query_kind={query_kind or 'missing'}")
            if evidence_kind not in {"submission", "comment"}:
                errors.append(f"invalid evidence_kind={evidence_kind or 'missing'}")
            if not subreddit:
                errors.append("missing subreddit")
            if not permalink:
                errors.append("missing permalink_url")
            if parse_datetime_utc(fetched_at) is None:
                errors.append("missing_or_invalid fetched_at_utc")
            if created_at is not None and parse_datetime_utc(created_at) is None:
                errors.append("invalid created_at_utc")
            if dedup_key:
                if dedup_key in seen_dedup_keys:
                    duplicate_rows += 1
                else:
                    seen_dedup_keys.add(dedup_key)
        if duplicate_rows > 0:
            errors.append(f"duplicate normalized dedup_key rows={duplicate_rows}")

        return RedditCorpusVerification(
            corpus_id=inspection.corpus_id,
            ok=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details={
                "source_id": self.source_id,
                "corpus_root": str(inspection.corpus_root),
                "checkpoint_path": str(inspection.checkpoint_path),
                "checkpoint_completed": inspection.checkpoint_completed,
                "raw_counts": dict(inspection.raw_counts),
                "normalized_counts": dict(inspection.normalized_counts),
                "duplicate_dedup_keys": duplicate_rows,
            },
        )

    def verify_source(
        self,
        *,
        subreddits: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
        include_comments: bool | None = None,
        comment_limit_per_post: int | None = None,
    ) -> RedditSourceVerification:
        queries = parse_reddit_query_values(subreddits=subreddits, keywords=keywords)
        if not queries:
            queries = (RedditQuery(value="worldnews", kind=RedditQueryKind.SUBREDDIT),)

        oauth_user = ""
        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, object] = {}

        try:
            oauth_probe = self.adapter.verify_oauth()
            oauth_user = str(oauth_probe.get("username") or "")
            details["oauth_probe"] = dict(oauth_probe)
        except RedditSourceFetchError as exc:
            errors.append(f"oauth_verify_failed reason={exc.reason_code} detail={exc.detail}")

        effective_limit = max(limit_per_query if limit_per_query is not None else self.default_limit_per_query, 1)
        effective_include_comments = (
            bool(include_comments) if include_comments is not None else self.default_include_comments
        )
        effective_comment_limit = max(
            comment_limit_per_post if comment_limit_per_post is not None else self.default_comment_limit_per_post,
            1,
        )
        fetched_records = 0

        for query in queries:
            try:
                page = self.adapter.fetch_submissions(query, limit=effective_limit, after=None)
            except RedditSourceFetchError as exc:
                errors.append(f"query={query.key} fetch_failed reason={exc.reason_code} detail={exc.detail}")
                continue
            fetched_records += len(page.records)
            query_detail: dict[str, object] = {
                "records": len(page.records),
                "next_cursor": page.next_cursor or "",
                "retries_used": page.retries_used,
                "rate_limit_remaining": page.rate_limit_remaining,
                "rate_limit_reset_sec": page.rate_limit_reset_sec,
                "rate_limit_used": page.rate_limit_used,
            }
            if effective_include_comments:
                comments_total = 0
                comment_errors = 0
                for record in page.records:
                    if record.evidence_kind != RedditEvidenceKind.SUBMISSION or not record.post_id:
                        continue
                    try:
                        comments_page = self.adapter.fetch_comments(
                            post_id=record.post_id,
                            query=query,
                            limit=effective_comment_limit,
                        )
                    except RedditSourceFetchError:
                        comment_errors += 1
                        continue
                    comments_total += len(comments_page.records)
                query_detail["comments_records"] = comments_total
                if comment_errors:
                    warnings.append(f"query={query.key} comments_probe_errors={comment_errors}")
            details[query.key] = query_detail

        return RedditSourceVerification(
            source_id=self.source_id,
            ok=not errors,
            checked_queries=queries,
            oauth_user=oauth_user,
            fetched_records=fetched_records,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    @staticmethod
    def _load_existing_raw_keys(storage: RedditCorpusStorage) -> set[tuple[str, str, str]]:
        rows: set[tuple[str, str, str]] = set()
        for row in storage.read_rows(normalized=False, name="reddit_payloads"):
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            dedup_key = str(row.get("dedup_key") or "").strip()
            evidence_kind = str(row.get("evidence_kind") or "").strip()
            if not query or not query_kind or not dedup_key or not evidence_kind:
                continue
            rows.add((f"{query_kind}:{query.lower()}", evidence_kind, dedup_key))
        return rows

    @staticmethod
    def _load_existing_normalized_keys(storage: RedditCorpusStorage) -> set[str]:
        rows: set[str] = set()
        for row in storage.read_rows(normalized=True, name="reddit_evidence"):
            dedup_key = str(row.get("dedup_key") or "").strip()
            if dedup_key:
                rows.add(dedup_key)
        return rows

    @staticmethod
    def _load_existing_subreddit_keys(storage: RedditCorpusStorage) -> set[str]:
        rows: set[str] = set()
        for row in storage.read_rows(normalized=True, name="reddit_subreddits"):
            subreddit = str(row.get("subreddit") or "").strip().lower()
            if subreddit:
                rows.add(subreddit)
        return rows

    @staticmethod
    def _persist_page_records(
        *,
        counters: _Counters,
        storage: RedditCorpusStorage,
        query: RedditQuery,
        page_records: tuple,
        existing_raw_keys: set[tuple[str, str, str]],
        existing_normalized_keys: set[str],
    ) -> _Counters:
        raw_rows_persisted = counters.raw_rows_persisted
        normalized_rows_persisted = counters.normalized_rows_persisted
        normalized_rows_deduped = counters.normalized_rows_deduped
        submissions_persisted = counters.submissions_persisted
        comments_persisted = counters.comments_persisted
        for record in page_records:
            raw_key = (query.key, record.evidence_kind.value, record.dedup_key)
            if raw_key not in existing_raw_keys:
                existing_raw_keys.add(raw_key)
                raw_rows_persisted += 1
                storage.append_raw("reddit_payloads", record.to_raw_dict())

            if record.dedup_key in existing_normalized_keys:
                normalized_rows_deduped += 1
                continue
            existing_normalized_keys.add(record.dedup_key)
            normalized_rows_persisted += 1
            storage.append_normalized("reddit_evidence", record.to_normalized_dict())
            if record.evidence_kind == RedditEvidenceKind.SUBMISSION:
                submissions_persisted += 1
            elif record.evidence_kind == RedditEvidenceKind.COMMENT:
                comments_persisted += 1
        return _Counters(
            query_count=counters.query_count,
            queries_processed=counters.queries_processed,
            queries_skipped=counters.queries_skipped,
            pages_fetched=counters.pages_fetched,
            source_failures=counters.source_failures,
            retries_used=counters.retries_used,
            raw_rows_persisted=raw_rows_persisted,
            normalized_rows_persisted=normalized_rows_persisted,
            normalized_rows_deduped=normalized_rows_deduped,
            submissions_persisted=submissions_persisted,
            comments_persisted=comments_persisted,
            subreddit_rows_persisted=counters.subreddit_rows_persisted,
        )

    @staticmethod
    def _persist_subreddit_metadata_rows(
        *,
        counters: _Counters,
        storage: RedditCorpusStorage,
        page_records: tuple,
        existing_subreddit_keys: set[str],
    ) -> _Counters:
        subreddit_rows_persisted = counters.subreddit_rows_persisted
        for record in page_records:
            subreddit = record.subreddit.strip().lower()
            if not subreddit:
                continue
            if subreddit in existing_subreddit_keys:
                continue
            metadata = dict(record.subreddit_metadata)
            if not metadata:
                continue
            existing_subreddit_keys.add(subreddit)
            payload = {
                "source_id": record.source_id,
                "source_class": record.source_class,
                "source_name": record.source_name,
                "subreddit": subreddit,
                "subreddit_id": str(metadata.get("subreddit_id") or record.subreddit_id),
                "fetched_at_utc": datetime.now(UTC).isoformat(),
                "metadata": metadata,
            }
            storage.append_raw("reddit_subreddits", payload)
            storage.append_normalized("reddit_subreddits", payload)
            subreddit_rows_persisted += 1
        added_rows = subreddit_rows_persisted - counters.subreddit_rows_persisted
        return _Counters(
            query_count=counters.query_count,
            queries_processed=counters.queries_processed,
            queries_skipped=counters.queries_skipped,
            pages_fetched=counters.pages_fetched,
            source_failures=counters.source_failures,
            retries_used=counters.retries_used,
            raw_rows_persisted=counters.raw_rows_persisted + added_rows,
            normalized_rows_persisted=counters.normalized_rows_persisted + added_rows,
            normalized_rows_deduped=counters.normalized_rows_deduped,
            submissions_persisted=counters.submissions_persisted,
            comments_persisted=counters.comments_persisted,
            subreddit_rows_persisted=subreddit_rows_persisted,
        )
