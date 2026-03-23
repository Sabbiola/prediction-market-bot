from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot.infrastructure import JsonlPersistence
from prediction_market_bot.services import evaluate_window, generate_report_markdown, replay_run


def _persistence(tmp_path: Path) -> JsonlPersistence:
    return JsonlPersistence(
        artifacts_dir=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit" / "events.jsonl",
    )


def _write_minimal_run(persistence: JsonlPersistence, *, run_id: str, market_id: str, fair_yes_prob: float, resolved_yes: bool) -> None:
    persistence.write_artifact(
        run_id,
        "market_snapshots",
        {"market": {"market_id": market_id}, "liquidity_usd": 20_000, "spread_bps": 100},
    )
    persistence.write_artifact(
        run_id,
        "market_candidates",
        {"market": {"market": {"market_id": market_id}}, "scan_score": 0.8},
    )
    persistence.write_artifact(run_id, "research_packets", {"market_id": market_id, "evidence_strength": 0.8})
    persistence.write_artifact(
        run_id,
        "prediction_results",
        {
            "market_id": market_id,
            "selected_side": "YES",
            "fair_yes_prob": fair_yes_prob,
            "confidence": 0.7,
        },
    )
    persistence.write_artifact(
        run_id,
        "risk_decisions",
        {
            "market_id": market_id,
            "approved": False,
            "reasoning": ("confidence_below_threshold confidence=0.5000 min=0.6200",),
        },
    )
    persistence.write_artifact(
        run_id,
        "execution_results",
        {"market_id": market_id, "status": "SKIPPED"},
    )
    persistence.write_artifact(
        run_id,
        "settlement_results",
        {
            "market_id": market_id,
            "resolved_yes": resolved_yes,
            "outcome_classification": "SKIPPED",
        },
    )
    persistence.write_artifact(
        run_id,
        "postmortems",
        {"market_id": market_id, "causes": ("CALIBRATION_ERROR",)},
    )
    persistence.write_artifact(
        run_id,
        "pipeline_summaries",
        {
            "run_id": run_id,
            "correlation_id": run_id,
            "status": "success",
            "total_markets": 1,
            "counters": {
                "candidate_markets": 1,
                "rejected_trades": 1,
                "executed_paper_trades": 0,
                "stale_data_events": 0,
                "source_failures": 0,
            },
            "stage_timings_ms": {"scan": 1.2, "research": 2.3, "total": 3.5},
        },
    )


def test_replay_run_reconstructs_decisions_and_metrics(tmp_path: Path) -> None:
    persistence = _persistence(tmp_path)
    _write_minimal_run(
        persistence,
        run_id="eval-run-1",
        market_id="m-1",
        fair_yes_prob=0.7,
        resolved_yes=True,
    )

    summary = replay_run(persistence, "eval-run-1")
    assert summary.run_id == "eval-run-1"
    assert len(summary.reconstructed_records) == 1
    record = summary.reconstructed_records[0]
    assert record.scan is not None
    assert record.research is not None
    assert record.prediction is not None
    assert record.risk is not None
    assert record.execution is not None

    assert summary.calibration_metrics.sample_size == 1
    assert summary.calibration_metrics.mean_fair_yes_prob == 0.7
    assert summary.calibration_metrics.observed_yes_rate == 1.0
    assert summary.brier_metrics.sample_size == 1
    assert summary.brier_metrics.fair_yes_brier_score == pytest.approx(0.09, abs=1e-9)
    assert summary.failure_categories.get("signal_quality", 0) >= 1
    assert summary.failure_categories.get("calibration_failure", 0) >= 1


def test_evaluate_window_aggregates_multiple_runs(tmp_path: Path) -> None:
    persistence = _persistence(tmp_path)
    _write_minimal_run(
        persistence,
        run_id="eval-run-1",
        market_id="m-1",
        fair_yes_prob=0.7,
        resolved_yes=True,
    )
    _write_minimal_run(
        persistence,
        run_id="eval-run-2",
        market_id="m-2",
        fair_yes_prob=0.3,
        resolved_yes=False,
    )

    window = evaluate_window(persistence, limit_runs=10)
    assert window.run_count == 2
    assert set(window.run_ids) == {"eval-run-1", "eval-run-2"}
    assert window.calibration_metrics.sample_size == 2
    assert window.brier_metrics.sample_size == 2
    assert window.total_markets == 2


def test_generate_report_markdown_includes_observability_section(tmp_path: Path) -> None:
    persistence = _persistence(tmp_path)
    _write_minimal_run(
        persistence,
        run_id="eval-run-obs",
        market_id="m-obs",
        fair_yes_prob=0.55,
        resolved_yes=True,
    )

    summary = replay_run(persistence, "eval-run-obs")
    report = generate_report_markdown(summary)

    assert "## Observability" in report
    assert "- correlation_id: eval-run-obs" in report
    assert "candidate_markets: 1" in report
    assert "stage_timings_ms" in report
