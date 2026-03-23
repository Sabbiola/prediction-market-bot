from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from time import perf_counter
from typing import Callable, Mapping
from urllib import parse, request
from xml.etree import ElementTree as ET

from prediction_market_bot.infrastructure.alt_data.news_models import (
    NewsArticleRecord,
    NewsFetchBatch,
    NewsQuery,
    NewsQueryKind,
    parse_datetime_utc,
)

_TAG_RE = re.compile(r"<[^>]+>")


class NewsSourceFetchError(RuntimeError):
    def __init__(self, *, source_id: str, reason_code: str, detail: str) -> None:
        super().__init__(f"news_source_fetch_error source={source_id} reason={reason_code} detail={detail}")
        self.source_id = source_id
        self.reason_code = reason_code
        self.detail = detail


@dataclass(slots=True, frozen=True)
class _CacheEntry:
    xml_payload: str
    expires_at_utc: datetime


def _default_fetch_text(url: str, timeout_sec: float, headers: Mapping[str, str]) -> str:
    req = request.Request(url=url, headers={str(k): str(v) for k, v in headers.items()}, method="GET")
    with request.urlopen(req, timeout=timeout_sec) as response:  # nosec B310
        payload = response.read()
    return payload.decode("utf-8", errors="replace")


class GoogleNewsRssAdapter:
    def __init__(
        self,
        *,
        source_id: str,
        source_class: str = "news_rss_web",
        source_name: str = "google-news-rss",
        endpoint_url: str = "https://news.google.com/rss",
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        retry_jitter_sec: float = 0.25,
        cache_ttl_sec: int = 600,
        language: str = "en-US",
        region: str = "US",
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        random_fn: Callable[[], float] | None = None,
        fetch_text_fn: Callable[[str, float, Mapping[str, str]], str] | None = None,
    ) -> None:
        self.source_id = source_id.strip().lower()
        self.source_class = source_class.strip().lower() or "news_rss_web"
        self.source_name = source_name.strip() or "google-news-rss"
        self.endpoint_url = (endpoint_url.strip() or "https://news.google.com/rss").rstrip("/")
        self.timeout_sec = max(timeout_sec, 0.1)
        self.max_retries = max(max_retries, 0)
        self.retry_backoff_sec = max(retry_backoff_sec, 0.0)
        self.retry_jitter_sec = max(retry_jitter_sec, 0.0)
        self.cache_ttl_sec = max(cache_ttl_sec, 0)
        self.language = language.strip() or "en-US"
        self.region = region.strip() or "US"
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or time.sleep
        self.random_fn = random_fn or random.random
        self.fetch_text_fn = fetch_text_fn or _default_fetch_text
        self._cache: dict[str, _CacheEntry] = {}

    def fetch(self, query: NewsQuery, *, limit: int = 50) -> NewsFetchBatch:
        started = perf_counter()
        effective_limit = max(limit, 1)
        feed_url = self._build_feed_url(query)
        xml_payload, retries_used, cache_hit = self._fetch_feed_xml(feed_url)
        fetched_at_utc = self.now_fn()
        records, feed_metadata = self._parse_feed(
            xml_payload=xml_payload,
            query=query,
            feed_url=feed_url,
            fetched_at_utc=fetched_at_utc,
            limit=effective_limit,
        )
        return NewsFetchBatch(
            source_id=self.source_id,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            feed_url=feed_url,
            fetched_at_utc=fetched_at_utc,
            retries_used=retries_used,
            cache_hit=cache_hit,
            duration_ms=round(max((perf_counter() - started) * 1000.0, 0.0), 3),
            feed_metadata=feed_metadata,
            records=records,
        )

    def _build_feed_url(self, query: NewsQuery) -> str:
        topic_or_keyword = query.value.strip()
        if not topic_or_keyword:
            raise NewsSourceFetchError(
                source_id=self.source_id,
                reason_code="empty_query",
                detail="query value is empty",
            )

        if query.kind == NewsQueryKind.TOPIC:
            topic = parse.quote(topic_or_keyword.upper(), safe="")
            language_code = self.language.split("-", maxsplit=1)[0].lower() or "en"
            base = f"{self.endpoint_url}/headlines/section/topic/{topic}"
            params = {
                "hl": self.language,
                "gl": self.region,
                "ceid": f"{self.region}:{language_code}",
            }
            return f"{base}?{parse.urlencode(params)}"

        language_code = self.language.split("-", maxsplit=1)[0].lower() or "en"
        params = {
            "q": topic_or_keyword,
            "hl": self.language,
            "gl": self.region,
            "ceid": f"{self.region}:{language_code}",
        }
        return f"{self.endpoint_url}/search?{parse.urlencode(params)}"

    def _fetch_feed_xml(self, feed_url: str) -> tuple[str, int, bool]:
        now = self.now_fn()
        cached = self._cache.get(feed_url)
        if cached is not None and cached.expires_at_utc >= now:
            return cached.xml_payload, 0, True

        headers = {
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            "User-Agent": "prediction-market-bot/0.1",
        }
        retries_used = 0
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                xml_payload = self.fetch_text_fn(feed_url, self.timeout_sec, headers)
                if self.cache_ttl_sec > 0:
                    self._cache[feed_url] = _CacheEntry(
                        xml_payload=xml_payload,
                        expires_at_utc=now + timedelta(seconds=self.cache_ttl_sec),
                    )
                return xml_payload, retries_used, False
            except Exception as exc:  # pragma: no cover - exercised via tests with fake fetcher
                last_error = exc
                if attempt >= self.max_retries:
                    break
                retries_used += 1
                delay = (self.retry_backoff_sec * (attempt + 1)) + (self.random_fn() * self.retry_jitter_sec)
                if delay > 0:
                    self.sleep_fn(delay)

        raise NewsSourceFetchError(
            source_id=self.source_id,
            reason_code="fetch_failed",
            detail=type(last_error).__name__ if last_error is not None else "unknown_error",
        )

    def _parse_feed(
        self,
        *,
        xml_payload: str,
        query: NewsQuery,
        feed_url: str,
        fetched_at_utc: datetime,
        limit: int,
    ) -> tuple[tuple[NewsArticleRecord, ...], dict[str, str]]:
        try:
            root = ET.fromstring(xml_payload)  # nosec B314
        except ET.ParseError as exc:
            raise NewsSourceFetchError(
                source_id=self.source_id,
                reason_code="invalid_rss_xml",
                detail=str(exc),
            ) from exc

        feed_title = self._extract_feed_title(root)
        feed_metadata = {
            "feed_url": feed_url,
            "feed_title": feed_title,
            "endpoint_url": self.endpoint_url,
        }

        records: list[NewsArticleRecord] = []
        for item in self._iter_feed_items(root):
            record = self._parse_item(
                item=item,
                query=query,
                feed_url=feed_url,
                feed_title=feed_title,
                fetched_at_utc=fetched_at_utc,
            )
            if record is None:
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return tuple(records), feed_metadata

    @staticmethod
    def _extract_feed_title(root: ET.Element) -> str:
        for elem in root:
            if GoogleNewsRssAdapter._local_name(elem.tag) == "channel":
                for child in elem:
                    if GoogleNewsRssAdapter._local_name(child.tag) != "title":
                        continue
                    text = GoogleNewsRssAdapter._node_text(child)
                    if text:
                        return text
        for child in root:
            if GoogleNewsRssAdapter._local_name(child.tag) != "title":
                continue
            text = GoogleNewsRssAdapter._node_text(child)
            if text:
                return text
        return ""

    @staticmethod
    def _iter_feed_items(root: ET.Element) -> tuple[ET.Element, ...]:
        rows: list[ET.Element] = []
        for elem in root.iter():
            tag = GoogleNewsRssAdapter._local_name(elem.tag)
            if tag in {"item", "entry"}:
                rows.append(elem)
        return tuple(rows)

    def _parse_item(
        self,
        *,
        item: ET.Element,
        query: NewsQuery,
        feed_url: str,
        feed_title: str,
        fetched_at_utc: datetime,
    ) -> NewsArticleRecord | None:
        title = self._first_child_text(item, ("title",))
        if not title:
            return None
        article_url = self._extract_item_url(item)
        if not article_url:
            return None
        summary = self._first_child_text(item, ("description", "summary", "content")) or title
        summary = self._strip_html(summary)
        publisher = self._first_child_text(item, ("source", "author")) or feed_title or parse.urlparse(article_url).netloc
        published_raw = self._first_child_text(item, ("pubDate", "published", "updated"))
        published_at_utc = self._parse_published_timestamp(published_raw)
        source_record_id = self._first_child_text(item, ("guid", "id")) or article_url
        dedup_key = self._build_dedup_key(
            article_url=article_url,
            title=title,
            published_at_utc=published_at_utc,
            source_record_id=source_record_id,
        )
        source_metadata = {
            "feed_url": feed_url,
            "feed_title": feed_title,
            "publisher": publisher,
        }
        raw_payload = {
            "title": title,
            "summary": summary,
            "article_url": article_url,
            "publisher": publisher,
            "published_at_raw": published_raw or "",
            "source_record_id": source_record_id,
            "query": query.value,
            "query_kind": query.kind.value,
        }
        return NewsArticleRecord(
            source_id=self.source_id,
            source_class=self.source_class,
            source_name=self.source_name,
            query=query.value,
            query_kind=query.kind,
            dedup_key=dedup_key,
            source_record_id=source_record_id,
            title=title,
            summary=summary,
            article_url=article_url,
            article_domain=parse.urlparse(article_url).netloc.lower(),
            publisher=publisher,
            published_at_utc=published_at_utc,
            fetched_at_utc=fetched_at_utc,
            source_metadata=source_metadata,
            raw_payload=raw_payload,
        )

    def _extract_item_url(self, item: ET.Element) -> str:
        link_text = self._first_child_text(item, ("link",))
        if link_text:
            return link_text
        for child in item:
            if self._local_name(child.tag) != "link":
                continue
            href = (child.attrib.get("href") or "").strip()
            if href:
                return href
        return ""

    @staticmethod
    def _node_text(node: ET.Element) -> str:
        text = "".join(node.itertext()).strip()
        return unescape(text)

    @classmethod
    def _first_child_text(cls, item: ET.Element, names: tuple[str, ...]) -> str:
        expected = {name.lower() for name in names}
        for child in item:
            tag = cls._local_name(child.tag).lower()
            if tag in expected:
                text = cls._node_text(child)
                if text:
                    return text
        return ""

    @staticmethod
    def _local_name(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", maxsplit=1)[-1]
        return tag

    @staticmethod
    def _strip_html(text: str) -> str:
        clean = _TAG_RE.sub(" ", text)
        return " ".join(unescape(clean).split())

    @staticmethod
    def _parse_published_timestamp(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = parse_datetime_utc(value)
        if parsed is not None:
            return parsed
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _build_dedup_key(
        *,
        article_url: str,
        title: str,
        published_at_utc: datetime | None,
        source_record_id: str,
    ) -> str:
        canonical = "|".join(
            [
                article_url.strip().lower(),
                title.strip().lower(),
                source_record_id.strip().lower(),
                published_at_utc.isoformat() if published_at_utc is not None else "",
            ]
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"news:sha256:{digest}"
