from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .models import EnrichmentInputRecord


class LlmEnrichmentProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_id(self) -> str:
        ...

    def enrich(self, record: EnrichmentInputRecord, *, prompt_version: str) -> Mapping[str, Any]:
        ...


class LlmEnrichmentProviderError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(slots=True, frozen=True)
class DeterministicEnrichmentProvider:
    provider_name: str = "deterministic_heuristic"
    model_id: str = "deterministic-enrichment-v1"

    _CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
    _POLICY_KEYWORDS = (
        "law",
        "bill",
        "policy",
        "senate",
        "congress",
        "government",
        "minister",
        "regulation",
    )
    _LEGAL_KEYWORDS = ("court", "judge", "appeal", "lawsuit", "supreme")
    _MACRO_KEYWORDS = ("inflation", "gdp", "rate", "recession", "jobs", "fed", "ecb")
    _EARNINGS_KEYWORDS = ("earnings", "revenue", "guidance", "quarter", "eps")
    _TECH_KEYWORDS = ("ai", "chip", "software", "platform", "launch", "product")
    _GEO_KEYWORDS = ("war", "ceasefire", "sanction", "election", "treaty", "border")
    _CONTRADICTION_MARKERS = (
        "however",
        "but",
        "despite",
        "although",
        "contradict",
        "on the other hand",
    )

    def enrich(self, record: EnrichmentInputRecord, *, prompt_version: str) -> Mapping[str, Any]:
        del prompt_version
        text = record.text.lower()
        relevance_score = self._score_relevance(record=record, text=text)
        extracted_claims = self._extract_claims(record.text)
        contradiction_indicators = self._extract_contradiction_indicators(text)
        contradiction_score = min(1.0, len(contradiction_indicators) * 0.25)
        novelty_score = self._score_novelty(record=record)
        catalyst_class = self._classify_catalyst(text)
        summary = self._build_summary(record=record, relevance_score=relevance_score, catalyst_class=catalyst_class)
        return {
            "relevance_score": relevance_score,
            "extracted_claims": extracted_claims,
            "contradiction_score": contradiction_score,
            "contradiction_indicators": contradiction_indicators,
            "novelty_score": novelty_score,
            "catalyst_class": catalyst_class,
            "structured_summary": summary,
        }

    def _score_relevance(self, *, record: EnrichmentInputRecord, text: str) -> float:
        score = max(min(record.linkage_confidence, 1.0), 0.0)
        market_terms = {token for token in re.findall(r"[a-z0-9]{3,}", record.market_title.lower())}
        overlap = 0
        for token in market_terms:
            if token in text:
                overlap += 1
        if market_terms:
            score = min(1.0, score + (0.25 * (overlap / len(market_terms))))
        if record.linkage_state == "ambiguous":
            score = max(0.0, score - 0.15)
        return round(score, 4)

    def _score_novelty(self, *, record: EnrichmentInputRecord) -> float:
        novelty = 0.55
        if record.published_at_utc is None:
            novelty -= 0.15
        if record.linkage_state == "linked":
            novelty += 0.10
        if record.source_class == "x":
            novelty += 0.05
        return round(max(0.0, min(1.0, novelty)), 4)

    def _extract_claims(self, text: str) -> tuple[str, ...]:
        stripped = text.strip()
        if not stripped:
            return ()
        parts = [row.strip() for row in self._CLAIM_SPLIT_RE.split(stripped) if row.strip()]
        if not parts:
            parts = [stripped]
        return tuple(parts[:3])

    def _extract_contradiction_indicators(self, text: str) -> tuple[str, ...]:
        hits: list[str] = []
        for marker in self._CONTRADICTION_MARKERS:
            if marker in text:
                hits.append(marker)
        return tuple(sorted(set(hits)))

    def _classify_catalyst(self, text: str) -> str:
        if any(keyword in text for keyword in self._POLICY_KEYWORDS):
            return "policy"
        if any(keyword in text for keyword in self._LEGAL_KEYWORDS):
            return "legal"
        if any(keyword in text for keyword in self._MACRO_KEYWORDS):
            return "macro"
        if any(keyword in text for keyword in self._EARNINGS_KEYWORDS):
            return "earnings"
        if any(keyword in text for keyword in self._TECH_KEYWORDS):
            return "technology"
        if any(keyword in text for keyword in self._GEO_KEYWORDS):
            return "geopolitics"
        if "regulat" in text:
            return "regulation"
        return "other"

    @staticmethod
    def _build_summary(*, record: EnrichmentInputRecord, relevance_score: float, catalyst_class: str) -> str:
        lead = record.title.strip() or "Evidence item"
        return (
            f"{lead}. Relevance={relevance_score:.2f}, catalyst={catalyst_class}, "
            f"source={record.source_class}, linkage_state={record.linkage_state}."
        )


@dataclass(slots=True, frozen=True)
class ExternalLlmProviderStub:
    provider_name: str = "external_llm_stub"
    model_id: str = "unconfigured"

    def enrich(self, record: EnrichmentInputRecord, *, prompt_version: str) -> Mapping[str, Any]:
        del record, prompt_version
        raise LlmEnrichmentProviderError(
            "External LLM provider is not configured for this environment.",
            reason_code="external_provider_unavailable",
        )
