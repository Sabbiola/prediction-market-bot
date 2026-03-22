from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.infrastructure.alt_data import XApiAdapter, XFetchPage, XPostRecord, XQuery, XQueryKind
from prediction_market_bot.infrastructure.alt_data.adapters.x_api_adapter import XSourceFetchError
from prediction_market_bot.infrastructure.alt_data.x_models import parse_datetime_utc

from .models import (
    XBackfillSummary,
    XCorpusInspection,
    XCorpusVerification,
    XSourceVerification,
    parse_x_query_values,
)
from .storage import NORMALIZED_FILES, RAW_FILES, XCorpusLayout, XCorpusStorage

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


class XCorpusService:
    def __init__(
        self,
        *,
        base_dir: Path,
        default_corpus_id: str,
        source_id: str,
        adapter: XApiAdapter,
        default_limit_per_query: int = 50,
        default_max_pages_per_query: int = 5,
        throttle_sec: float = 0.0,
    ) -> None:
        self.base_dir = base_dir
        self.default_corpus_id = default_corpus_id
        self.source_id = source_id.strip().lower()
        self.adapter = adapter
        self.default_limit_per_query = max(default_limit_per_query, 1)
        self.default_max_pages_per_query = max(default_max_pages_per_query, 0)
        self.throttle_sec = max(throttle_sec, 0.0)

    def backfill_x(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
        reset_checkpoint: bool = False,
        accounts: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
        max_pages_per_query: int | None = None,
        incremental: bool = False,
    ) -> XBackfillSummary:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        queries = parse_x_query_values(accounts=accounts, keywords=keywords)
        if not queries:
            raise ValueError("At least one query is required. Use --account and/or --keyword.")

        layout = XCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = XCorpusStorage(layout)
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

        existing_raw_keys = self._load_existing_raw_keys(storage)
        existing_normalized_keys = self._load_existing_normalized_keys(storage)
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
                )
                continue

            if self.throttle_sec > 0 and query_index > 0:
                time.sleep(self.throttle_sec)

            cursor = None if incremental else checkpoint.query_cursors.get(query.key)
            pages_for_query = 0
            query_failed = False

            while True:
                if self.throttle_sec > 0 and pages_for_query > 0:
                    time.sleep(self.throttle_sec)

                try:
                    page = self._fetch_query_page(query, limit=effective_limit, cursor=cursor)
                except XSourceFetchError as exc:
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
                )
                counters = self._persist_page_records(
                    counters=counters,
                    storage=storage,
                    query=query,
                    page_records=page.records,
                    existing_raw_keys=existing_raw_keys,
                    existing_normalized_keys=existing_normalized_keys,
                )

                cursor = page.next_cursor
                if cursor:
                    checkpoint.query_cursors[query.key] = cursor
                else:
                    checkpoint.query_cursors.pop(query.key, None)
                    if not incremental:
                        checkpoint.completed_query_keys.add(query.key)
                checkpoint.updated_at_utc = datetime.now(UTC)
                storage.save_checkpoint(checkpoint)

                if not cursor:
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
            "x_corpus_backfill_completed",
            extra={
                "event": "x_corpus_backfill_completed",
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
            },
        )
        return XBackfillSummary(
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
            warnings=tuple(warnings),
        )

    def inspect_corpus(
        self,
        *,
        corpus_id: str | None = None,
        checkpoint_path: Path | None = None,
    ) -> XCorpusInspection:
        effective_corpus_id = (corpus_id or self.default_corpus_id).strip() or self.default_corpus_id
        layout = XCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=effective_corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = XCorpusStorage(layout)
        checkpoint = storage.load_checkpoint(corpus_id=effective_corpus_id, source_id=self.source_id)
        return XCorpusInspection(
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
    ) -> XCorpusVerification:
        inspection = self.inspect_corpus(corpus_id=corpus_id, checkpoint_path=checkpoint_path)
        layout = XCorpusLayout.from_base(
            base_dir=self.base_dir,
            corpus_id=inspection.corpus_id,
            checkpoint_path=checkpoint_path,
        )
        storage = XCorpusStorage(layout)
        evidence_rows = storage.read_rows(normalized=True, name="x_evidence")

        errors: list[str] = []
        warnings: list[str] = []
        if not evidence_rows:
            warnings.append("normalized/x_evidence.jsonl is empty")

        seen_dedup_keys: set[str] = set()
        duplicate_rows = 0
        for row in evidence_rows:
            dedup_key = str(row.get("dedup_key") or "").strip()
            source_record_id = str(row.get("source_record_id") or "").strip()
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            post_id = str(row.get("post_id") or "").strip()
            post_url = str(row.get("post_url") or "").strip()
            fetched_at = str(row.get("fetched_at_utc") or "").strip()
            created_at = row.get("created_at_utc")
            if not dedup_key:
                errors.append("missing dedup_key")
            if not source_record_id:
                errors.append("missing source_record_id")
            if not query:
                errors.append("missing query")
            if query_kind not in {"keyword", "account"}:
                errors.append(f"invalid query_kind={query_kind or 'missing'}")
            if not post_id:
                errors.append("missing post_id")
            if not post_url:
                errors.append("missing post_url")
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

        return XCorpusVerification(
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
        accounts: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        limit_per_query: int | None = None,
    ) -> XSourceVerification:
        queries = parse_x_query_values(accounts=accounts, keywords=keywords)
        if not queries:
            queries = (XQuery(value="markets", kind=XQueryKind.KEYWORD),)

        errors: list[str] = []
        warnings: list[str] = []
        details: dict[str, object] = {}
        effective_limit = max(limit_per_query if limit_per_query is not None else self.default_limit_per_query, 1)
        fetched_records = 0

        for query in queries:
            try:
                page = self._fetch_query_page(query, limit=effective_limit, cursor=None)
            except XSourceFetchError as exc:
                errors.append(f"query={query.key} fetch_failed reason={exc.reason_code} detail={exc.detail}")
                continue
            fetched_records += len(page.records)
            details[query.key] = {
                "records": len(page.records),
                "next_cursor": page.next_cursor or "",
                "retries_used": page.retries_used,
                "rate_limit_remaining": page.rate_limit_remaining,
                "rate_limit_reset_epoch": page.rate_limit_reset_epoch,
                "query_kind": query.kind.value,
            }
            if query.kind == XQueryKind.ACCOUNT and page.next_cursor:
                warnings.append(f"query={query.key} account_has_more_pages")

        return XSourceVerification(
            source_id=self.source_id,
            ok=not errors,
            checked_queries=queries,
            fetched_records=fetched_records,
            errors=tuple(errors),
            warnings=tuple(warnings),
            details=details,
        )

    def _fetch_query_page(self, query: XQuery, *, limit: int, cursor: str | None) -> XFetchPage:
        if query.kind == XQueryKind.KEYWORD:
            if not self.adapter.search_endpoint_available:
                raise XSourceFetchError(
                    source_id=self.source_id,
                    reason_code="search_endpoint_unavailable",
                    detail="search endpoint path is not configured",
                )
            return self.adapter.fetch_search(query, limit=limit, cursor=cursor)
        if self.adapter.auth_mode != "user_context":
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="auth_mode_insufficient",
                detail="account queries require auth_mode=user_context",
            )
        if not self.adapter.account_endpoints_available:
            raise XSourceFetchError(
                source_id=self.source_id,
                reason_code="account_endpoint_unavailable",
                detail="account endpoints are not configured",
            )
        return self.adapter.fetch_account_posts(query, limit=limit, cursor=cursor)

    @staticmethod
    def _load_existing_raw_keys(storage: XCorpusStorage) -> set[tuple[str, str]]:
        rows: set[tuple[str, str]] = set()
        for row in storage.read_rows(normalized=False, name="x_payloads"):
            query = str(row.get("query") or "").strip()
            query_kind = str(row.get("query_kind") or "").strip()
            dedup_key = str(row.get("dedup_key") or "").strip()
            if not query or not query_kind or not dedup_key:
                continue
            rows.add((f"{query_kind}:{query.lower()}", dedup_key))
        return rows

    @staticmethod
    def _load_existing_normalized_keys(storage: XCorpusStorage) -> set[str]:
        rows: set[str] = set()
        for row in storage.read_rows(normalized=True, name="x_evidence"):
            dedup_key = str(row.get("dedup_key") or "").strip()
            if dedup_key:
                rows.add(dedup_key)
        return rows

    @staticmethod
    def _persist_page_records(
        *,
        counters: _Counters,
        storage: XCorpusStorage,
        query: XQuery,
        page_records: tuple[XPostRecord, ...],
        existing_raw_keys: set[tuple[str, str]],
        existing_normalized_keys: set[str],
    ) -> _Counters:
        raw_rows_persisted = counters.raw_rows_persisted
        normalized_rows_persisted = counters.normalized_rows_persisted
        normalized_rows_deduped = counters.normalized_rows_deduped
        for record in page_records:
            raw_key = (query.key, record.dedup_key)
            if raw_key not in existing_raw_keys:
                existing_raw_keys.add(raw_key)
                raw_rows_persisted += 1
                storage.append_raw("x_payloads", record.to_raw_dict())

            if record.dedup_key in existing_normalized_keys:
                normalized_rows_deduped += 1
                continue
            existing_normalized_keys.add(record.dedup_key)
            normalized_rows_persisted += 1
            storage.append_normalized("x_evidence", record.to_normalized_dict())

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
        )
