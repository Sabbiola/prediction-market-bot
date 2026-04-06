from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.main import main


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _set_slow_stage_threshold(app_cfg: Path, *, threshold_ms: float) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    observability = raw.setdefault("observability", {})
    if not isinstance(observability, dict):
        raise AssertionError("observability must be a mapping")
    performance = observability.setdefault("performance", {})
    if not isinstance(performance, dict):
        raise AssertionError("observability.performance must be a mapping")
    performance["slow_stage_threshold_ms"] = threshold_ms
    raw["observability"] = observability
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_run_once_emits_slow_stage_event_when_threshold_low(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    run_id = f"{deterministic_run_id}-perf-slow-stage"
    _set_slow_stage_threshold(app_cfg, threshold_ms=0.001)
    # Force static market data so the test is deterministic regardless of live
    # Polymarket availability.  Without this the scan may return 0 candidates
    # (live API rate-limit / transient failure) and prediction/risk stages are
    # never entered, causing the assertion on stage_timings to fail.
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw.setdefault("live_market_data", {})["enabled"] = False
    raw.setdefault("runtime", {})["market_data_provider"] = "STATIC"
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    exit_code = main(
        [
            "run-once",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            run_id,
        ]
    )
    assert exit_code == 0

    audit_rows = _read_jsonl(app_cfg.parent / "audit" / "events.jsonl")
    assert any(
        row.get("run_id") == run_id and row.get("event_type") == "slow_stage_detected"
        for row in audit_rows
    )

    summary_rows = _read_jsonl(app_cfg.parent / "artifacts" / "pipeline_summaries.jsonl")
    run_summary = next(row for row in summary_rows if row.get("run_id") == run_id)
    payload = run_summary.get("payload", {})
    assert isinstance(payload, dict)
    stage_timings = payload.get("stage_timings_ms")
    assert isinstance(stage_timings, dict)
    assert "prediction" in stage_timings
    assert "risk" in stage_timings
