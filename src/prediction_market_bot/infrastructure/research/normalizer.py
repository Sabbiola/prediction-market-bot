from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from prediction_market_bot.domain.models import ResearchFinding

from .models import ResearchNormalizationContext

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


class ResearchFindingNormalizer:
    @staticmethod
    def build_query(title: str, market_id: str) -> str:
        words = [word for word in _WORD_RE.findall(title.lower()) if len(word) > 2 and word not in _STOPWORDS]
        if not words:
            return market_id
        return " ".join(words[:8])

    @staticmethod
    def strip_html(text: str) -> str:
        clean = _HTML_TAG_RE.sub(" ", text)
        return " ".join(clean.split())

    @staticmethod
    def tokenize(text: str) -> set[str]:
        return {token for token in _WORD_RE.findall(text.lower()) if token not in _STOPWORDS}

    @classmethod
    def relevance_score(cls, query: str, text: str) -> float:
        query_tokens = cls.tokenize(query)
        text_tokens = cls.tokenize(text)
        if not query_tokens or not text_tokens:
            return 0.0
        overlap = len(query_tokens & text_tokens)
        return cls.clamp(overlap / len(query_tokens), lower=0.0, upper=1.0)

    @staticmethod
    def estimate_sentiment(text: str) -> float:
        tokens = _WORD_RE.findall(text.lower())
        if not tokens:
            return 0.0
        positive = sum(1 for token in tokens if token in _POSITIVE_WORDS)
        negative = sum(1 for token in tokens if token in _NEGATIVE_WORDS)
        raw = (positive - negative) / max(len(tokens), 4)
        return round(ResearchFindingNormalizer.clamp(raw * 3.0, lower=-1.0, upper=1.0), 4)

    @classmethod
    def confidence(
        cls,
        *,
        base_confidence: float,
        relevance: float,
        published_at: datetime | None,
        fetched_at: datetime,
    ) -> float:
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
        relevance_factor = 0.5 + (0.5 * cls.clamp(relevance, lower=0.0, upper=1.0))
        value = base_confidence * recency_factor * relevance_factor
        return round(cls.clamp(value, lower=0.05, upper=1.0), 4)

    @staticmethod
    def parse_timestamp(value: Any) -> datetime | None:
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
    def first_str(record: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = record.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
        return None

    @staticmethod
    def clamp(value: float, *, lower: float, upper: float) -> float:
        return min(max(value, lower), upper)

    @staticmethod
    def unique_strings(values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in values:
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return tuple(ordered)

    @classmethod
    def build_finding(
        cls,
        *,
        context: ResearchNormalizationContext,
        summary: str,
        url: str,
        relevance_text: str,
        published_at: datetime | None,
        base_confidence: float,
        provenance_extra: Sequence[str],
    ) -> ResearchFinding:
        relevance = cls.relevance_score(context.query, relevance_text)
        confidence = cls.confidence(
            base_confidence=base_confidence,
            relevance=relevance,
            published_at=published_at,
            fetched_at=context.fetched_at,
        )
        sentiment = cls.estimate_sentiment(summary)
        provenance = cls.unique_strings(
            (
                f"source={context.source_name}",
                f"source_type={context.source_type.value}",
                f"endpoint={context.endpoint_url}",
                f"query={context.query}",
                f"fetched_at={context.fetched_at.isoformat()}",
                f"published_at={published_at.isoformat() if published_at else ''}",
                *tuple(provenance_extra),
            )
        )
        return ResearchFinding(
            source_type=context.source_type,
            source_name=context.source_name,
            summary=summary,
            sentiment=sentiment,
            credibility=confidence,
            url=url,
            provenance=provenance,
        )
