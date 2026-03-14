from __future__ import annotations

import json
from pathlib import Path

import yaml
from prediction_market_bot.main import main
from prediction_market_bot.services import PaperPortfolioEngine


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _latest_request_states(rows: list[dict[str, object]], *, run_id: str) -> dict[str, str]:
    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        if row.get("run_id") != run_id:
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        request_id = str(payload.get("request_id", "")).strip()
        state = str(payload.get("state", "")).strip()
        timestamp = str(row.get("timestamp", ""))
        if not request_id:
            continue
        current = latest.get(request_id)
        if current is None or timestamp > current[0]:
            latest[request_id] = (timestamp, state)
    return {request_id: state for request_id, (_, state) in latest.items()}


def test_settlement_lane_can_run_decoupled_from_run_once(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    app_payload = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    app_payload.setdefault("execution", {})
    app_payload["execution"]["settlement_same_run"] = False
    app_payload["execution"]["blocking_trade_review"] = False
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
    agents_payload["risk"]["min_bet_usd"] = 1.0
    agents_cfg.write_text(yaml.safe_dump(agents_payload, sort_keys=False), encoding="utf-8")

    run_exit = main(
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
    assert run_exit == 0

    artifacts_dir = app_cfg.parent / "artifacts"
    pending = _read_jsonl(artifacts_dir / "pending_settlement_requests.jsonl")
    request_states_before = _latest_request_states(pending, run_id=deterministic_run_id)
    assert request_states_before
    assert all(state == "PENDING" for state in request_states_before.values())

    settled_before = _read_jsonl(artifacts_dir / "settlement_results.jsonl")
    assert not any(row.get("run_id") == deterministic_run_id for row in settled_before)

    portfolio = PaperPortfolioEngine()
    portfolio.replay_rows(_read_jsonl(artifacts_dir / "paper_portfolio_events.jsonl"))
    assert portfolio.snapshot().open_position_count >= 1

    settle_exit = main(
        [
            "settle-run",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert settle_exit == 0

    settled_after = _read_jsonl(artifacts_dir / "settlement_results.jsonl")
    postmortems = _read_jsonl(artifacts_dir / "postmortems.jsonl")
    assert any(row.get("run_id") == deterministic_run_id for row in settled_after)
    assert any(row.get("run_id") == deterministic_run_id for row in postmortems)
    request_states_after = _latest_request_states(
        _read_jsonl(artifacts_dir / "pending_settlement_requests.jsonl"),
        run_id=deterministic_run_id,
    )
    assert request_states_after
    assert all(state == "SETTLED" for state in request_states_after.values())

    portfolio_after = PaperPortfolioEngine()
    portfolio_after.replay_rows(_read_jsonl(artifacts_dir / "paper_portfolio_events.jsonl"))
    assert portfolio_after.snapshot().open_position_count == 0
