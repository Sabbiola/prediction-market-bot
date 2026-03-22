from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.strategy_research.benchmarking import (
    ABLATION_VARIANT_NAMES,
    LabelRecord,
    StrategyResearchBenchmarkService,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _label_row(idx: int) -> LabelRecord:
    ts = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=idx)
    label_yes = 1 if idx % 2 == 0 else 0
    market_prob = 0.62 if label_yes == 1 else 0.38
    return LabelRecord(
        row_id=f"m-{idx}@{ts.isoformat()}",
        market_id=f"m-{idx}",
        event_id=f"e-{idx}",
        category="politics" if idx % 3 == 0 else "macro",
        market_title=f"Market {idx}",
        decision_timestamp_utc=ts,
        resolved_at_utc=ts + timedelta(days=2),
        resolved_outcome="YES" if label_yes == 1 else "NO",
        label_yes=label_yes,
        market_yes_prob_at_decision=market_prob,
        liquidity_usd=10000.0 + (idx * 10.0),
        volume_24h_usd=2000.0 + (idx * 5.0),
        structure_momentum=0.02 if label_yes == 1 else -0.02,
        structure_score=0.55 if label_yes == 1 else 0.45,
        scan_score=0.6,
        research_weighted_sentiment=0.3 if label_yes == 1 else -0.3,
        research_evidence_strength=0.7,
        research_disagreement_score=0.2,
        research_findings_count=4,
        data_quality_flags=(),
    )


def test_run_ablation_and_compare_alt_variants(tmp_path: Path) -> None:
    dataset_id = "hist-ds"
    historical_base = tmp_path / "historical"
    derived = historical_base / dataset_id / "derived"
    labels_path = derived / "labels.jsonl"
    labels = [_label_row(idx) for idx in range(1, 33)]
    _write_jsonl(labels_path, [row.to_dict() for row in labels])

    alt_rows_path = derived / "alt_feature_store" / "alt-v1" / "alt_feature_rows.jsonl"
    alt_rows: list[dict[str, object]] = []
    for row in labels:
        favorable = row.label_yes == 1
        alt_rows.append(
            {
                "row_id": row.row_id,
                "f_alt_news_volume_24h": 6 if favorable else 2,
                "f_alt_news_burstiness_24h": 1.2 if favorable else -0.3,
                "f_alt_source_diversity": 0.75 if favorable else 0.35,
                "f_alt_freshness_decay": 0.8 if favorable else 0.4,
                "f_alt_source_credibility_prior": 0.72,
                "f_alt_reddit_mentions_24h": 5 if favorable else 1,
                "f_alt_reddit_attention": 1.5 if favorable else 0.2,
                "f_alt_x_mentions_24h": 4 if favorable else 1,
                "f_alt_x_attention": 1.2 if favorable else 0.1,
                "f_alt_market_linked_coverage": 0.9 if favorable else 0.6,
                "f_alt_contradiction_score": 0.15 if favorable else 0.45,
                "f_alt_novelty_score": 0.68 if favorable else 0.22,
                "f_alt_catalyst_strength_score": 0.74 if favorable else 0.35,
                "f_alt_enrichment_coverage": 0.9 if favorable else 0.55,
            }
        )
    _write_jsonl(alt_rows_path, alt_rows)

    service = StrategyResearchBenchmarkService(
        historical_base_dir=historical_base,
        research_corpus_base_dir=tmp_path / "corpus",
        default_dataset_id=dataset_id,
        default_corpus_id="corp-a",
        prediction_settings=PredictionSettings(),
    )

    run_summary = service.run_ablation_study(
        split_mode="walk-forward",
        train_days=12,
        validation_days=6,
        test_days=6,
        step_days=3,
        max_folds=3,
    )
    assert run_summary.run_id.startswith("ablation-")
    assert run_summary.run_path.exists()
    assert run_summary.report_path.exists()
    assert set(run_summary.variants) == set(ABLATION_VARIANT_NAMES)
    test_payload = run_summary.aggregate.get("test", {})
    assert "market_only_baseline" in test_payload
    assert "market_plus_alt_data_with_llm_enrichment" in test_payload
    assert "walk_forward_robustness" in test_payload["market_only_baseline"]
    assert "approval_rate_delta_vs_market_only" in test_payload["market_plus_news"]

    comparison = service.compare_alt_data_variants(
        run_id=run_summary.run_id,
        split="test",
        reference_variant="market_only_baseline",
    )
    assert comparison.run_id == run_summary.run_id
    assert comparison.report_path.exists()
    assert len(comparison.ranking) == len(ABLATION_VARIANT_NAMES)
    assert "market_plus_alt_data_with_llm_enrichment" in comparison.variant_deltas
