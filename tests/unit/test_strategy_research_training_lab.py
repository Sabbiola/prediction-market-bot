from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.strategy_research.training import StrategyTrainingLabService


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _seed_feature_rows(base_dir: Path, dataset_id: str) -> Path:
    rows_path = base_dir / dataset_id / "derived" / "feature_store" / "v1" / "feature_rows.jsonl"
    rows: list[dict[str, object]] = []
    for index in range(24):
        day = index + 1
        prob = 0.2 + (index * 0.03)
        prob = min(max(prob, 0.05), 0.95)
        label = 1 if index % 2 == 0 else 0
        sentiment = 0.4 if label == 1 else -0.4
        rows.append(
            {
                "row_id": f"m-{index+1}@2026-01-{day:02d}T00:00:00+00:00",
                "market_id": f"m-{index+1}",
                "event_id": f"e-{index+1}",
                "category": "politics" if label == 1 else "macro",
                "decision_timestamp_utc": f"2026-01-{day:02d}T00:00:00+00:00",
                "label_yes": label,
                "f_market_yes_price": prob,
                "f_market_no_price": 1.0 - prob,
                "f_market_price_logit": (prob / (1.0 - prob)),
                "f_market_price_distance_0_5": abs(prob - 0.5),
                "f_spread_bps": 100 + index,
                "f_liquidity_usd": 10000 + (index * 100),
                "f_volume_24h_usd": 2000 + (index * 50),
                "f_price_move_24h": 0.08 if label == 1 else -0.08,
                "f_momentum_24h": 0.08 if label == 1 else -0.08,
                "f_realized_volatility_24h": 0.02 + (index * 0.001),
                "f_research_weighted_sentiment": sentiment,
                "f_research_evidence_strength": 0.7,
                "f_research_disagreement": 0.2,
                "f_research_findings_count": 3,
            }
        )
    _write_jsonl(rows_path, rows)
    return rows_path


def test_training_lab_train_compare_calibrate_and_model_card(tmp_path: Path) -> None:
    base_dir = tmp_path / "historical"
    dataset_id = "ds-1"
    _seed_feature_rows(base_dir, dataset_id)

    service = StrategyTrainingLabService(
        historical_base_dir=base_dir,
        default_dataset_id=dataset_id,
    )
    summary = service.train_baseline_models(include_xgboost=False)
    assert summary.run_id.startswith("train-")
    assert summary.best_model in {"logistic_regression_baseline", "tree_baseline"}
    assert summary.models["logistic_regression_baseline"].status == "trained"
    assert summary.models["tree_baseline"].status == "trained"
    assert summary.models["logistic_regression_baseline"].metrics_by_split["test"].sample_count > 0
    assert summary.run_path.exists()
    logistic_artifact = summary.run_path.parent / "models" / "logistic_regression_baseline.json"
    assert logistic_artifact.exists()
    logistic_payload = json.loads(logistic_artifact.read_text(encoding="utf-8"))
    assert logistic_payload["artifact_type"] == "prediction_model_v2"
    assert logistic_payload["feature_schema_version"] == "v1"
    assert logistic_payload["model_version"] == summary.run_id

    comparison = service.compare_models(run_id=summary.run_id, split="test")
    assert comparison.run_id == summary.run_id
    assert len(comparison.ranking) >= 1
    assert comparison.ranking[0][0] in {"logistic_regression_baseline", "tree_baseline"}

    calibration = service.calibrate_model(
        run_id=summary.run_id,
        model_name="logistic_regression_baseline",
        method="platt",
        fit_split="validation",
        eval_split="test",
    )
    assert calibration.run_id.startswith("cal-")
    assert calibration.calibration_path.exists()
    assert calibration.calibrated_metrics.sample_count > 0
    calibration_payload = json.loads(calibration.calibration_path.read_text(encoding="utf-8"))
    assert calibration_payload["artifact_type"] == "prediction_calibration_v2"
    assert calibration_payload["calibration_version"] == calibration.run_id

    model_card = service.generate_model_card(
        run_id=summary.run_id,
        model_name="logistic_regression_baseline",
        calibration_run_id=calibration.run_id,
        decision="needs_more_evidence",
    )
    assert model_card.output_path.exists()
    card_text = model_card.output_path.read_text(encoding="utf-8")
    assert "Model Card" in card_text
    assert "logistic_regression_baseline" in card_text
