from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence
from urllib import error, parse, request

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.interfaces import PersistencePort, ResearchDataPort

_WORD_RE = re.compile(r"[a-z0-9]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
}
_POSITIVE_WORDS = {"win", "wins", "approved", "approval", "growth", "gain", "up", "increase", "bullish", "surge"}
_NEGATIVE_WORDS = {"lose", "loss", "rejected", "decline", "down", "drop", "bearish", "ban", "decrease", "risk"}


@dataclass(slots=True, frozen=True)
class SourceFetchBatch:
    source_name: str
    source_type: SourceType
    query: str
    raw_count: int
    normalized_count: int
    retries_used: int
    cache_hit: bool
    findings: tuple[ResearchFinding, ...]
    raw_records: tuple[dict[str, Any], ...]


@dataclass(slots=True, frozen=True)
class ResearchIngestionBatch:
    run_id: str
    market_id: str
    source_count: int
    raw_count: int
    normalized_count: int
    deduplicated_count: int
    retries_used: int
    cache_hits: int
    findings: tuple[ResearchFinding, ...]


@dataclass(slots=True, frozen=True)
class _CacheEntry:
    expires_at: datetime
    payload: Any


class StructuredHttpResearchSource(ResearchDataPort):
    """Base class for read-only structured research sources over HTTP JSON APIs."""

    def __init__(
        self,
        *,
        source_name: str,
        source_type: SourceType,
        endpoint_url: str,
        base_confidence: float,
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        cache_ttl_seconds: int = 300,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.source_name = source_name
        self.source_type = source_type
        self.endpoint_url = endpoint_url
        self.base_confidence = self._clamp(base_confidence, lower=0.05, upper=1.0)
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec
        self.cache_ttl_seconds = max(cache_ttl_seconds, 0)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self._cache: dict[str, _CacheEntry] = {}

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        return self.fetch_with_meta(market).findings

    def fetch_with_meta(
        self,
        market: MarketSnapshot,
        *,
        query_override: str | None = None,
        limit: int = 5,
    ) -> SourceFetchBatch:
        query = query_override.strip() if isinstance(query_override, str) and query_override.strip() else self._build_query(market)
        payload, retries_used, cache_hit = self._fetch_payload(query=query, limit=limit)
        records = self._extract_records(payload)
        fetched_at = self.now_fn()

        findings: list[ResearchFinding] = []
        raw_records: list[dict[str, Any]] = []
        for record in records:
            raw_records.append(dict(record))
            finding = self._normalize_record(record, market=market, query=query, fetched_at=fetched_at)
            if finding is not None:
                findings.append(finding)

        ordered = tuple(
            sorted(
                findings,
                key=lambda finding: (
                    -finding.credibility,
                    finding.source_name,
                    finding.summary,
                    finding.url,
                ),
            )
        )
        return SourceFetchBatch(
            source_name=self.source_name,
            source_type=self.source_type,
            query=query,
            raw_count=len(raw_records),
            normalized_count=len(ordered),
            retries_used=retries_used,
            cache_hit=cache_hit,
            findings=ordered,
            raw_records=tuple(raw_records),
        )

    def _fetch_payload(self, *, query: str, limit: int) -> tuple[Any, int, bool]:
        cache_key = f"{query}|{max(limit, 1)}"
        cached = self._cache.get(cache_key)
        now = self.now_fn()
        if cached and cached.expires_at >= now:
            return cached.payload, 0, True

        retries_used = 0
        last_error: Exception | None = None
        url = self._build_url(query=query, limit=limit)

        for attempt in range(self.max_retries + 1):
            try:
                req = request.Request(url=url, headers={"Accept": "application/json"}, method="GET")
                with request.urlopen(req, timeout=self.timeout_sec) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if self.cache_ttl_seconds > 0:
                    self._cache[cache_key] = _CacheEntry(
                        expires_at=now + timedelta(seconds=self.cache_ttl_seconds),
                        payload=payload,
                    )
                return payload, retries_used, False
            except (
                error.URLError,
                error.HTTPError,
                TimeoutError,
                socket.timeout,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                retries_used += 1
                time.sleep(self.retry_backoff_sec * (attempt + 1))

        raise RuntimeError(f"{self.source_name}_fetch_failed: {last_error}") from last_error

    def _build_url(self, *, query: str, limit: int) -> str:
        raise NotImplementedError

    def _extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        raise NotImplementedError

    def _normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        market: MarketSnapshot,
        query: str,
        fetched_at: datetime,
    ) -> ResearchFinding | None:
        raise NotImplementedError

    @staticmethod
    def _build_query(market: MarketSnapshot) -> str:
        words = [word for word in _WORD_RE.findall(market.title.lower()) if len(word) > 2 and word not in _STOPWORDS]
        if not words:
            return market.market_id
        return " ".join(words[:8])

    @staticmethod
    def _strip_html(text: str) -> str:
        clean = _HTML_TAG_RE.sub(" ", text)
        return " ".join(clean.split())

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in _WORD_RE.findall(text.lower()) if token not in _STOPWORDS}

    @classmethod
    def _relevance_score(cls, query: str, text: str) -> float:
        query_tokens = cls._tokenize(query)
        text_tokens = cls._tokenize(text)
        if not query_tokens or not text_tokens:
            return 0.0
        overlap = len(query_tokens & text_tokens)
        return cls._clamp(overlap / len(query_tokens), lower=0.0, upper=1.0)

    @staticmethod
    def _estimate_sentiment(text: str) -> float:
        tokens = _WORD_RE.findall(text.lower())
        if not tokens:
            return 0.0
        positive = sum(1 for token in tokens if token in _POSITIVE_WORDS)
        negative = sum(1 for token in tokens if token in _NEGATIVE_WORDS)
        raw = (positive - negative) / max(len(tokens), 4)
        return round(StructuredHttpResearchSource._clamp(raw * 3.0, lower=-1.0, upper=1.0), 4)

    def _confidence(self, *, relevance: float, published_at: datetime | None, fetched_at: datetime) -> float:
        recency_factor = 0.75
        if published_at is not None:
            age_hours = max((fetched_at - published_at).total_seconds() / 3600.0, 0.0)
            if age_hours <= 24:
                recency_factor = 1.0
            elif age_hours <= 72:
                recency_factor = 0.9
            elif age_hours <= 168:
                recency_factor = 0.8
            elif age_hours <= 720:
                recency_factor = 0.7
            else:
                recency_factor = 0.6
        relevance_factor = 0.5 + (0.5 * self._clamp(relevance, lower=0.0, upper=1.0))
        value = self.base_confidence * recency_factor * relevance_factor
        return round(self._clamp(value, lower=0.05, upper=1.0), 4)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
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
            # Handle YYYY-MM-DD values that fromisoformat parses as naive datetimes.
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

    @staticmethod
    def _first_str(record: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = record.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
        return None

    @staticmethod
    def _clamp(value: float, *, lower: float, upper: float) -> float:
        return min(max(value, lower), upper)


class WikipediaSearchResearchSource(StructuredHttpResearchSource):
    def __init__(
        self,
        *,
        endpoint_url: str = "https://en.wikipedia.org/w/api.php",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        cache_ttl_seconds: int = 600,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            source_name="wikipedia-search",
            source_type=SourceType.RSS,
            endpoint_url=endpoint_url,
            base_confidence=0.58,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            cache_ttl_seconds=cache_ttl_seconds,
            now_fn=now_fn,
        )

    def _build_url(self, *, query: str, limit: int) -> str:
        params = {
            "action": "query",
            "list": "search",
            "format": "json",
            "utf8": "1",
            "srsearch": query,
            "srlimit": str(max(limit, 1)),
        }
        querystring = parse.urlencode(params)
        separator = "&" if "?" in self.endpoint_url else "?"
        return f"{self.endpoint_url}{separator}{querystring}"

    def _extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            return []
        query_section = payload.get("query")
        if not isinstance(query_section, Mapping):
            return []
        rows = query_section.get("search")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, Mapping)]

    def _normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        market: MarketSnapshot,
        query: str,
        fetched_at: datetime,
    ) -> ResearchFinding | None:
        del market
        title = self._first_str(record, ("title",))
        snippet_raw = self._first_str(record, ("snippet",)) or ""
        snippet = self._strip_html(snippet_raw)
        summary = snippet or (title or "")
        if not summary:
            return None

        page_id = record.get("pageid")
        page_id_text = str(page_id).strip() if page_id is not None else ""
        url = f"https://en.wikipedia.org/?curid={page_id_text}" if page_id_text else ""
        published_at = self._parse_timestamp(record.get("timestamp"))
        relevance = self._relevance_score(query, f"{title or ''} {summary}")
        confidence = self._confidence(relevance=relevance, published_at=published_at, fetched_at=fetched_at)
        sentiment = self._estimate_sentiment(summary)

        provenance = self._build_provenance(
            query=query,
            fetched_at=fetched_at,
            record_id=page_id_text or "unknown",
            published_at=published_at,
            extra=(f"title={title or 'unknown'}",),
        )
        return ResearchFinding(
            source_type=self.source_type,
            source_name=self.source_name,
            summary=summary,
            sentiment=sentiment,
            credibility=confidence,
            url=url,
            provenance=provenance,
        )

    def _build_provenance(
        self,
        *,
        query: str,
        fetched_at: datetime,
        record_id: str,
        published_at: datetime | None,
        extra: tuple[str, ...],
    ) -> tuple[str, ...]:
        values = [
            f"source={self.source_name}",
            f"source_type={self.source_type.value}",
            f"endpoint={self.endpoint_url}",
            f"record_id={record_id}",
            f"query={query}",
            f"fetched_at={fetched_at.isoformat()}",
            f"published_at={published_at.isoformat() if published_at else ''}",
            *extra,
        ]
        return tuple(item for item in values if item)


class OpenAlexWorksResearchSource(StructuredHttpResearchSource):
    def __init__(
        self,
        *,
        endpoint_url: str = "https://api.openalex.org/works",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        cache_ttl_seconds: int = 600,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            source_name="openalex-works",
            source_type=SourceType.OFFICIAL,
            endpoint_url=endpoint_url,
            base_confidence=0.72,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            cache_ttl_seconds=cache_ttl_seconds,
            now_fn=now_fn,
        )

    def _build_url(self, *, query: str, limit: int) -> str:
        params = {
            "search": query,
            "per-page": str(max(limit, 1)),
            "sort": "relevance_score:desc",
        }
        querystring = parse.urlencode(params)
        separator = "&" if "?" in self.endpoint_url else "?"
        return f"{self.endpoint_url}{separator}{querystring}"

    def _extract_records(self, payload: Any) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            return []
        rows = payload.get("results")
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, Mapping)]

    def _normalize_record(
        self,
        record: Mapping[str, Any],
        *,
        market: MarketSnapshot,
        query: str,
        fetched_at: datetime,
    ) -> ResearchFinding | None:
        del market
        title = self._first_str(record, ("display_name", "title"))
        if title is None:
            return None

        primary_location = record.get("primary_location")
        landing_page_url = ""
        venue_name = ""
        if isinstance(primary_location, Mapping):
            landing_page_url = self._first_str(primary_location, ("landing_page_url", "pdf_url")) or ""
            source_info = primary_location.get("source")
            if isinstance(source_info, Mapping):
                venue_name = self._first_str(source_info, ("display_name",)) or ""

        concepts = record.get("concepts")
        concept_name = ""
        if isinstance(concepts, list):
            for item in concepts:
                if isinstance(item, Mapping):
                    concept_name = self._first_str(item, ("display_name",)) or ""
                    if concept_name:
                        break

        summary_parts = [title]
        if concept_name:
            summary_parts.append(f"Topic: {concept_name}")
        if venue_name:
            summary_parts.append(f"Venue: {venue_name}")
        summary = " | ".join(summary_parts)

        published_at = self._parse_timestamp(record.get("publication_date"))
        relevance = self._relevance_score(query, f"{title} {concept_name} {venue_name}")
        confidence = self._confidence(relevance=relevance, published_at=published_at, fetched_at=fetched_at)
        sentiment = self._estimate_sentiment(summary)
        record_id = self._first_str(record, ("id", "doi")) or "unknown"
        url = landing_page_url or record_id

        provenance = self._build_provenance(
            query=query,
            fetched_at=fetched_at,
            record_id=record_id,
            published_at=published_at,
            concept=concept_name,
        )
        return ResearchFinding(
            source_type=self.source_type,
            source_name=self.source_name,
            summary=summary,
            sentiment=sentiment,
            credibility=confidence,
            url=url,
            provenance=provenance,
        )

    def _build_provenance(
        self,
        *,
        query: str,
        fetched_at: datetime,
        record_id: str,
        published_at: datetime | None,
        concept: str,
    ) -> tuple[str, ...]:
        values = [
            f"source={self.source_name}",
            f"source_type={self.source_type.value}",
            f"endpoint={self.endpoint_url}",
            f"record_id={record_id}",
            f"query={query}",
            f"fetched_at={fetched_at.isoformat()}",
            f"published_at={published_at.isoformat() if published_at else ''}",
            f"concept={concept}",
        ]
        return tuple(item for item in values if item)


class LiveResearchIngestionPipeline(ResearchDataPort):
    """Aggregates structured research sources with deduplication and traceability."""

    def __init__(
        self,
        *,
        sources: Sequence[StructuredHttpResearchSource],
        persistence: PersistencePort | None = None,
        now_fn: Callable[[], datetime] | None = None,
        similarity_threshold: float = 0.7,
    ) -> None:
        self.sources = tuple(sources)
        self.persistence = persistence
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.similarity_threshold = self._clamp(similarity_threshold, lower=0.5, upper=1.0)

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        return self.ingest(market, run_id=None).findings

    def ingest(
        self,
        market: MarketSnapshot,
        *,
        run_id: str | None = None,
        query_override: str | None = None,
        limit_per_source: int = 5,
    ) -> ResearchIngestionBatch:
        effective_run_id = run_id or f"research-ingest-{self.now_fn().strftime('%Y%m%d%H%M%S')}"
        normalized: list[ResearchFinding] = []
        raw_count = 0
        retries_used = 0
        cache_hits = 0

        for source in self.sources:
            batch = source.fetch_with_meta(
                market,
                query_override=query_override,
                limit=max(limit_per_source, 1),
            )
            raw_count += batch.raw_count
            retries_used += batch.retries_used
            cache_hits += int(batch.cache_hit)
            normalized.extend(batch.findings)
            self._persist_source_batch(effective_run_id, market.market_id, batch)

        deduplicated = self._deduplicate(normalized)
        for finding in deduplicated:
            self._persist_normalized(effective_run_id, market.market_id, finding)

        self._write_event(
            effective_run_id,
            {
                "market_id": market.market_id,
                "source_count": len(self.sources),
                "raw_count": raw_count,
                "normalized_count": len(normalized),
                "deduplicated_count": len(deduplicated),
                "retries_used": retries_used,
                "cache_hits": cache_hits,
            },
        )

        return ResearchIngestionBatch(
            run_id=effective_run_id,
            market_id=market.market_id,
            source_count=len(self.sources),
            raw_count=raw_count,
            normalized_count=len(normalized),
            deduplicated_count=len(deduplicated),
            retries_used=retries_used,
            cache_hits=cache_hits,
            findings=deduplicated,
        )

    def _deduplicate(self, findings: Sequence[ResearchFinding]) -> tuple[ResearchFinding, ...]:
        ordered = sorted(findings, key=lambda item: (-item.credibility, item.source_name, item.summary, item.url))
        deduplicated: list[ResearchFinding] = []
        signatures: list[set[str]] = []

        for finding in ordered:
            tokens = self._tokenize(finding.summary)
            match_index = self._find_similar(finding, tokens, deduplicated, signatures)
            if match_index is None:
                deduplicated.append(finding)
                signatures.append(tokens)
                continue

            merged = self._merge_duplicates(deduplicated[match_index], finding)
            deduplicated[match_index] = merged
            signatures[match_index] = self._tokenize(merged.summary)

        return tuple(sorted(deduplicated, key=lambda item: (-item.credibility, item.source_name, item.summary, item.url)))

    def _find_similar(
        self,
        finding: ResearchFinding,
        tokens: set[str],
        existing: Sequence[ResearchFinding],
        signatures: Sequence[set[str]],
    ) -> int | None:
        for index, current in enumerate(existing):
            if finding.url and current.url and finding.url == current.url:
                return index
            similarity = self._jaccard(tokens, signatures[index])
            if similarity >= self.similarity_threshold:
                return index
            current_summary = current.summary.lower()
            new_summary = finding.summary.lower()
            if current_summary in new_summary or new_summary in current_summary:
                return index
        return None

    def _merge_duplicates(self, primary: ResearchFinding, duplicate: ResearchFinding) -> ResearchFinding:
        merged_provenance = self._unique_strings(
            (
                *primary.provenance,
                *duplicate.provenance,
                f"duplicate_source={duplicate.source_name}",
            )
        )
        merged_confidence = round(
            self._clamp(
                max(primary.credibility, duplicate.credibility) + (min(primary.credibility, duplicate.credibility) * 0.08),
                lower=0.05,
                upper=1.0,
            ),
            4,
        )
        merged_url = primary.url or duplicate.url
        return primary.model_copy(
            update={
                "credibility": merged_confidence,
                "url": merged_url,
                "provenance": merged_provenance,
            }
        )

    def _persist_source_batch(self, run_id: str, market_id: str, batch: SourceFetchBatch) -> None:
        if not self.persistence:
            return
        for row in batch.raw_records:
            self.persistence.write_artifact(
                run_id,
                "raw_research_findings",
                {
                    "market_id": market_id,
                    "source_name": batch.source_name,
                    "source_type": batch.source_type.value,
                    "query": batch.query,
                    "payload": row,
                },
            )
        for finding in batch.findings:
            self.persistence.write_artifact(
                run_id,
                "normalized_research_findings",
                {
                    "market_id": market_id,
                    "source_name": batch.source_name,
                    "source_type": batch.source_type.value,
                    "finding": finding.model_dump(mode="json"),
                },
            )

    def _persist_normalized(self, run_id: str, market_id: str, finding: ResearchFinding) -> None:
        if not self.persistence:
            return
        self.persistence.write_artifact(
            run_id,
            "deduped_research_findings",
            {
                "market_id": market_id,
                "finding": finding.model_dump(mode="json"),
            },
        )

    def _write_event(self, run_id: str, payload: Mapping[str, Any]) -> None:
        if self.persistence:
            self.persistence.write_run_event(run_id, "research_ingestion_end", payload)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in _WORD_RE.findall(text.lower()) if token not in _STOPWORDS}

    @staticmethod
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        union = a | b
        if not union:
            return 0.0
        return len(a & b) / len(union)

    @staticmethod
    def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return tuple(ordered)

    @staticmethod
    def _clamp(value: float, *, lower: float, upper: float) -> float:
        return min(max(value, lower), upper)


def build_live_research_sources(
    *,
    wikipedia_endpoint_url: str = "https://en.wikipedia.org/w/api.php",
    openalex_endpoint_url: str = "https://api.openalex.org/works",
    timeout_sec: float = 8.0,
    max_retries: int = 2,
    retry_backoff_sec: float = 0.5,
    cache_ttl_seconds: int = 600,
    now_fn: Callable[[], datetime] | None = None,
) -> tuple[StructuredHttpResearchSource, ...]:
    return (
        WikipediaSearchResearchSource(
            endpoint_url=wikipedia_endpoint_url,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            cache_ttl_seconds=cache_ttl_seconds,
            now_fn=now_fn,
        ),
        OpenAlexWorksResearchSource(
            endpoint_url=openalex_endpoint_url,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            retry_backoff_sec=retry_backoff_sec,
            cache_ttl_seconds=cache_ttl_seconds,
            now_fn=now_fn,
        ),
    )
