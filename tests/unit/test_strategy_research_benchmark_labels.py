from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.strategy_research.benchmarking import StrategyResearchBenchmarkService


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row))
            handle.write("\n")


def test_build_labels_generates_reproducible_rows(tmp_path: Path) -> None:
    historical_base = tmp_path / "historical"
    dataset_id = "hist-ds"
    dataset_root = historical_base / dataset_id / "normalized"
    _write_jsonl(
        dataset_root / "markets.jsonl",
        [
            {
                "market_id": "m-1",
                "event_id": "e-1",
                "question": "Will event happen?",
                "category": "macro",
                "close_at_utc": "2026-01-10T00:00:00+00:00",
                "resolved_outcome": "YES",
                "resolved_at_utc": "2026-01-11T00:00:00+00:00",
                "yes_price_last": 0.56,
                "liquidity_usd": 12000,
                "volume_24h_usd": 3500,
            },
            {
                "market_id": "m-2",
                "event_id": "e-2",
                "question": "Will another event happen?",
                "category": "politics",
                "close_at_utc": "2026-01-12T00:00:00+00:00",
                "resolved_outcome": "NO",
                "resolved_at_utc": "2026-01-13T00:00:00+00:00",
                "yes_price_last": 0.44,
                "liquidity_usd": 15000,
                "volume_24h_usd": 4700,
            },
        ],
    )
    _write_jsonl(
        dataset_root / "resolutions.jsonl",
        [
            {"market_id": "m-1", "resolved_outcome": "YES", "resolved_at_utc": "2026-01-11T00:00:00+00:00"},
            {"market_id": "m-2", "resolved_outcome": "NO", "resolved_at_utc": "2026-01-13T00:00:00+00:00"},
        ],
    )
    _write_jsonl(
        dataset_root / "market_snapshots.jsonl",
        [
            {"market_id": "m-1", "snapshot_at_utc": "2026-01-08T00:00:00+00:00", "yes_price": 0.50},
            {"market_id": "m-1", "snapshot_at_utc": "2026-01-09T00:00:00+00:00", "yes_price": 0.56},
            {"market_id": "m-2", "snapshot_at_utc": "2026-01-10T00:00:00+00:00", "yes_price": 0.49},
            {"market_id": "m-2", "snapshot_at_utc": "2026-01-11T00:00:00+00:00", "yes_price": 0.44},
        ],
    )

    corpus_base = tmp_path / "corpus"
    corpus_root = corpus_base / "corp-a" / "normalized"
    _write_jsonl(
        corpus_root / "evidence_findings.jsonl",
        [
            {
                "market_id": "m-1",
                "event_id": "e-1",
                "decision_timestamp_utc": "2026-01-10T00:00:00+00:00",
                "source_type": "RSS",
                "sentiment": 0.3,
                "credibility": 0.8,
                "is_time_aligned": True,
            },
            {
                "market_id": "m-2",
                "event_id": "e-2",
                "decision_timestamp_utc": "2026-01-12T00:00:00+00:00",
                "source_type": "OFFICIAL",
                "sentiment": -0.2,
                "credibility": 0.9,
                "is_time_aligned": True,
            },
        ],
    )

    service = StrategyResearchBenchmarkService(
        historical_base_dir=historical_base,
        research_corpus_base_dir=corpus_base,
        default_dataset_id=dataset_id,
        default_corpus_id="corp-a",
        prediction_settings=PredictionSettings(),
    )
    summary = service.build_labels()
    assert summary.labels_written == 2
    labels_path = Path(summary.labels_path)
    lines = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["label_yes"] in {0, 1}
    assert lines[0]["research_findings_count"] >= 0
