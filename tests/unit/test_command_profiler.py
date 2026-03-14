from __future__ import annotations

from prediction_market_bot.app.performance import CommandProfiler


def test_command_profiler_records_stage_payload() -> None:
    profiler = CommandProfiler(enabled=True)
    profiler.record("stage-a", 12.5)
    profiler.record("stage-a", 7.5)
    profiler.record("stage-b", 5.0)

    payload = profiler.payload()
    stage_totals = payload["stage_timings_ms"]
    stage_calls = payload["stage_calls"]
    stage_avg = payload["stage_avg_ms"]

    assert isinstance(stage_totals, dict)
    assert isinstance(stage_calls, dict)
    assert isinstance(stage_avg, dict)
    assert stage_totals["stage-a"] == 20.0
    assert stage_calls["stage-a"] == 2
    assert stage_avg["stage-a"] == 10.0
    assert stage_calls["stage-b"] == 1


def test_command_profiler_disabled_has_no_stages() -> None:
    profiler = CommandProfiler(enabled=False)
    profiler.record("stage-a", 15.0)
    payload = profiler.payload()
    assert payload["enabled"] is False
    assert payload["stage_timings_ms"] == {}
    assert payload["stage_calls"] == {}
