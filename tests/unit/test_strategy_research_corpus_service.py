from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketSnapshot, ResearchFinding
from prediction_market_bot.infrastructure.research import SourceFetchBatch
from prediction_market_bot.strategy_research.research_corpus import ResearchEvidenceArchivalService


def _write_source_market_dataset(base_dir: Path, dataset_id: str, *, decision_timestamp: str) -> None:
    normalized_dir = base_dir / dataset_id / "normalized"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    markets_path = normalized_dir / "markets.jsonl"
    markets_path.write_text(
        json.dumps(
            {
                "market_id": "m-1",
                "event_id": "e-1",
                "question": "Will event X happen?",
                "category": "macro",
                "close_at_utc": decision_timestamp,
                "resolved_at_utc": "2026-01-11T00:00:00+00:00",
                "resolved_outcome": "YES",
                "yes_price_last": 0.55,
                "liquidity_usd": 10000,
                "volume_24h_usd": 2300,
            }
        )
        + "\n",
        encoding="utf-8",
    )


class _FakeSource:
    source_name = "fake-source"

    def __init__(self, findings: tuple[ResearchFinding, ...], raw_records: tuple[dict[str, object], ...]) -> None:
        self._findings = findings
        self._raw_records = raw_records

    def fetch_with_meta(
        self,
        market: MarketSnapshot,
        *,
        query_override: str | None = None,
        limit: int = 5,
    ) -> SourceFetchBatch:
        del market, query_override, limit
        return SourceFetchBatch(
            source_name=self.source_name,
            source_type=SourceType.RSS,
            query="event x happen",
            raw_count=len(self._raw_records),
            normalized_count=len(self._findings),
            retries_used=0,
            cache_hit=False,
            duration_ms=1.0,
            findings=self._findings,
            raw_records=self._raw_records,
        )


def _finding(summary: str, *, url: str, published: str | None) -> ResearchFinding:
    provenance = ["query=event x happen", "fetched_at=2026-01-09T12:00:00+00:00", "source=fake-source"]
    if published is not None:
        provenance.append(f"published_at={published}")
    return ResearchFinding(
        source_type=SourceType.RSS,
        source_name="fake-source",
        summary=summary,
        sentiment=0.2,
        credibility=0.8,
        url=url,
        provenance=tuple(provenance),
    )


def test_research_corpus_backfill_deduplicates_and_filters_unaligned(tmp_path: Path) -> None:
    source_dataset_base = tmp_path / "source-datasets"
    _write_source_market_dataset(source_dataset_base, "hist-ds", decision_timestamp="2026-01-10T00:00:00+00:00")
    fake_source = _FakeSource(
        findings=(
            _finding("Aligned evidence", url="https://example.test/a", published="2026-01-09T10:00:00+00:00"),
            _finding("Aligned evidence", url="https://example.test/a", published="2026-01-09T10:00:00+00:00"),
            _finding("Post decision evidence", url="https://example.test/b", published="2026-01-12T10:00:00+00:00"),
            _finding("Missing published", url="https://example.test/c", published=None),
        ),
        raw_records=(
            {"id": "rec-a", "payload": "A"},
            {"id": "rec-b", "payload": "B"},
        ),
    )
    service = ResearchEvidenceArchivalService(
        base_dir=tmp_path / "research-corpus",
        source_dataset_base_dir=source_dataset_base,
        default_corpus_id="corpus-a",
        default_source_dataset_id="hist-ds",
        sources=(fake_source,),
        default_limit_per_source=5,
        throttle_sec=0.0,
        require_published_at=True,
        drop_unaligned=True,
    )

    summary = service.backfill_research_evidence()
    assert summary.decision_points_processed == 1
    assert summary.normalized_findings_persisted == 1
    assert summary.normalized_findings_deduped == 1
    assert summary.findings_filtered_post_decision >= 1
    assert summary.findings_filtered_missing_published_at >= 1

    inspection = service.inspect_corpus()
    assert inspection.normalized_counts["evidence_findings"] == 1
    assert inspection.raw_counts["source_payloads"] == 2

    evidence_file = summary.corpus_root / "normalized" / "evidence_findings.jsonl"
    rows = [json.loads(line) for line in evidence_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["source_record_id"]
    assert row["finding_identity"]
    assert isinstance(row["provenance"], list)
    assert row["alignment_reason"] == "aligned"

    verification = service.verify_alignment()
    assert verification.ok is True


def test_research_alignment_verify_fails_for_post_decision_rows(tmp_path: Path) -> None:
    source_dataset_base = tmp_path / "source-datasets"
    _write_source_market_dataset(source_dataset_base, "hist-ds", decision_timestamp="2026-01-10T00:00:00+00:00")
    fake_source = _FakeSource(
        findings=(
            _finding("Late evidence", url="https://example.test/late", published="2026-01-11T00:00:00+00:00"),
        ),
        raw_records=({"id": "rec-late", "payload": "late"},),
    )
    service = ResearchEvidenceArchivalService(
        base_dir=tmp_path / "research-corpus",
        source_dataset_base_dir=source_dataset_base,
        default_corpus_id="corpus-a",
        default_source_dataset_id="hist-ds",
        sources=(fake_source,),
        default_limit_per_source=5,
        throttle_sec=0.0,
        require_published_at=False,
        drop_unaligned=False,
    )

    summary = service.backfill_research_evidence()
    assert summary.normalized_findings_persisted == 1

    verification = service.verify_alignment()
    assert verification.ok is False
    assert any("post-decision evidence" in error for error in verification.errors)
