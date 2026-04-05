from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.main import main


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_pending_review_then_operator_approval_allows_execution(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    app_payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    app_payload.setdefault("runtime", {})
    app_payload["runtime"]["mode"] = "PAPER_LIVE"
    app_payload["runtime"]["market_data_provider"] = "STATIC"
    app_payload["runtime"]["research_provider"] = "STATIC"
    app_payload.setdefault("execution", {})
    app_payload["execution"]["blocking_trade_review"] = False
    app_payload["execution"]["review_auto_approve"] = False  # test validates blocking without auto-approve
    app_payload["execution"]["settlement_same_run"] = False
    app_payload.setdefault("feature_flags", {})
    app_payload["feature_flags"]["enable_manual_review_queue"] = False
    app_cfg.write_text(yaml.safe_dump(app_payload, sort_keys=False), encoding="utf-8")

    agents_payload = yaml.safe_load(agents_cfg.read_text(encoding="utf-8")) or {}
    agents_payload.setdefault("thresholds", {})
    agents_payload["thresholds"]["min_confidence"] = 0.0
    agents_payload["thresholds"]["min_edge_bps"] = -10000
    agents_payload.setdefault("agents", {})
    prediction_cfg = agents_payload["agents"].setdefault("prediction", {})  # type: ignore[index]
    prediction_components = prediction_cfg.setdefault("components", {})  # type: ignore[assignment]
    prediction_components["market_weight"] = 0.0
    prediction_components["narrative_weight"] = 0.8
    prediction_components["structure_weight"] = 0.2
    agents_payload.setdefault("risk", {})
    agents_payload["risk"]["min_bet_usd"] = 0.0
    agents_cfg.write_text(yaml.safe_dump(agents_payload, sort_keys=False), encoding="utf-8")

    first_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert first_exit == 0

    artifacts_dir = app_cfg.parent / "artifacts"
    queue_rows = _read_jsonl(artifacts_dir / "trade_review_candidates.jsonl")
    gate_rows = _read_jsonl(artifacts_dir / "trade_review_gate_decisions.jsonl")
    execution_rows = _read_jsonl(artifacts_dir / "execution_results.jsonl")
    queue_ids = [
        row["payload"]["queue_id"]  # type: ignore[index]
        for row in queue_rows
        if row.get("run_id") == deterministic_run_id and isinstance(row.get("payload"), dict)
    ]
    assert queue_ids
    assert any(
        row.get("run_id") == deterministic_run_id
        and isinstance(row.get("payload"), dict)
        and not bool(row["payload"].get("allowed", True))  # type: ignore[index]
        for row in gate_rows
    )
    assert any(
        row.get("run_id") == deterministic_run_id
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("status") == "SKIPPED"  # type: ignore[index]
        for row in execution_rows
    )

    approve_exit = main(
        [
            "review-approve",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--queue-id",
            str(queue_ids[0]),
            "--operator-id",
            "operator-e2e",
            "--rationale",
            "Approved for execution after manual review.",
        ]
    )
    assert approve_exit == 0

    second_exit = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert second_exit == 0

    gate_rows_after = _read_jsonl(artifacts_dir / "trade_review_gate_decisions.jsonl")
    execution_rows_after = _read_jsonl(artifacts_dir / "execution_results.jsonl")
    assert any(
        row.get("run_id") == deterministic_run_id
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("queue_id") == queue_ids[0]  # type: ignore[index]
        and bool(row["payload"].get("allowed", False))  # type: ignore[index]
        for row in gate_rows_after
    )
    assert any(
        row.get("run_id") == deterministic_run_id
        and isinstance(row.get("payload"), dict)
        and row["payload"].get("status") == "FILLED"  # type: ignore[index]
        for row in execution_rows_after
    )
