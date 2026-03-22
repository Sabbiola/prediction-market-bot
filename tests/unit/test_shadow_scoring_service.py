from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot.infrastructure import JsonlPersistence
from prediction_market_bot.services.history import (
    build_shadow_scoring_report,
    render_shadow_scoring_report_markdown,
)


def _persistence(tmp_path: Path) -> JsonlPersistence:
    return JsonlPersistence(
        artifacts_dir=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit" / "events.jsonl",
    )


def test_shadow_scoring_report_computes_deltas_and_disagreement_buckets(tmp_path: Path) -> None:
    persistence = _persistence(tmp_path)
    run_id = "shadow-run-1"
    persistence.write_artifact(
        run_id,
        "prediction_shadow_comparisons",
        {
            "market_id": "m-1",
            "heuristic_prediction": {"fair_yes_prob": 0.60, "edge": 0.05, "confidence": 0.70},
            "model_v2_prediction": {"fair_yes_prob": 0.70, "edge": 0.10, "confidence": 0.80},
            "alt_llm_shadow_prediction": {"fair_yes_prob": 0.66, "edge": 0.07, "confidence": 0.78},
            "approvals": {"heuristic": True, "model_v2": True},
            "parity_warnings": [],
            "disagreement_bucket": "major_drift",
            "alt_llm_disagreement_bucket": "minor_drift",
            "alt_llm_shadow_status": "available",
        },
    )
    persistence.write_artifact(
        run_id,
        "prediction_shadow_comparisons",
        {
            "market_id": "m-2",
            "heuristic_prediction": {"fair_yes_prob": 0.35, "edge": -0.02, "confidence": 0.40},
            "model_v2_prediction": None,
            "alt_llm_shadow_prediction": None,
            "approvals": {"heuristic": False, "model_v2": None, "alt_llm_shadow": None},
            "parity_warnings": ["feature_parity_check_failed feature_missing=f_missing"],
            "disagreement_bucket": "model_missing",
            "alt_llm_disagreement_bucket": "model_missing",
            "alt_llm_shadow_status": "inference_error",
        },
    )
    persistence.write_artifact(
        run_id,
        "settlement_results",
        {"market_id": "m-1", "resolved_yes": True},
    )

    report = build_shadow_scoring_report(persistence, run_id)
    assert report.total_rows == 2
    assert report.rows_with_model_v2 == 1
    assert report.rows_with_alt_llm_shadow == 1
    assert report.rows_with_parity_warnings == 1
    assert report.disagreement_buckets == {"major_drift": 1, "model_missing": 1}
    assert report.alt_llm_disagreement_buckets == {"minor_drift": 1, "model_missing": 1}
    assert report.calibration["sample_size"] == 1
    assert report.calibration["heuristic_brier"] == pytest.approx(0.16, abs=1e-8)
    assert report.calibration["model_v2_brier"] == pytest.approx(0.09, abs=1e-8)
    assert report.calibration["brier_delta_model_minus_heuristic"] == pytest.approx(-0.07, abs=1e-8)
    assert report.calibration_alt_llm["sample_size"] == 1
    assert report.calibration_alt_llm["alt_llm_brier"] == pytest.approx(0.1156, abs=1e-8)
    assert report.edge["mean_edge_delta_model_minus_heuristic"] == pytest.approx(0.05, abs=1e-8)
    assert report.edge_alt_llm["mean_edge_delta_alt_llm_minus_heuristic"] == pytest.approx(0.02, abs=1e-8)
    assert report.prediction_delta["mean_model_v2_minus_heuristic"] == pytest.approx(0.10, abs=1e-8)
    assert report.prediction_delta["mean_alt_llm_minus_heuristic"] == pytest.approx(0.06, abs=1e-8)
    assert report.confidence_delta["mean_model_v2_minus_heuristic"] == pytest.approx(0.10, abs=1e-8)
    assert report.confidence_delta["mean_alt_llm_minus_heuristic"] == pytest.approx(0.08, abs=1e-8)
    assert report.approval_rate["heuristic_rate"] == pytest.approx(0.5, abs=1e-8)
    assert report.approval_rate["model_v2_rate"] == pytest.approx(0.5, abs=1e-8)
    assert report.approval_rate_alt_llm["alt_llm_rate"] == pytest.approx(0.0, abs=1e-8)
    markdown = render_shadow_scoring_report_markdown(report)
    assert "## Calibration Deltas" in markdown
    assert "## Edge Deltas" in markdown
    assert "## Prediction Deltas" in markdown
    assert "## Confidence Deltas" in markdown
    assert "## Approval Rate Deltas" in markdown
    assert "## Disagreement Buckets" in markdown


def test_shadow_scoring_report_warns_when_run_has_no_shadow_rows(tmp_path: Path) -> None:
    persistence = _persistence(tmp_path)
    report = build_shadow_scoring_report(persistence, "run-without-shadow")
    assert report.total_rows == 0
    assert "no_prediction_shadow_comparisons_for_run" in report.warnings
