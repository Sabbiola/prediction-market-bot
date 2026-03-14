from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Iterator


@dataclass(slots=True)
class _StageAccumulator:
    total_ms: float = 0.0
    calls: int = 0


@dataclass(slots=True, frozen=True)
class StageTiming:
    stage: str
    total_ms: float
    calls: int
    avg_ms: float


class CommandProfiler:
    """Low-overhead stage timer for CLI/UI instrumentation."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._started = perf_counter()
        self._stages: dict[str, _StageAccumulator] = {}

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = perf_counter()
        try:
            yield
        finally:
            self.record(stage, (perf_counter() - started) * 1000.0)

    def record(self, stage: str, duration_ms: float) -> None:
        if not self.enabled:
            return
        key = stage.strip()
        if not key:
            return
        accumulator = self._stages.get(key)
        if accumulator is None:
            accumulator = _StageAccumulator()
            self._stages[key] = accumulator
        accumulator.total_ms += max(float(duration_ms), 0.0)
        accumulator.calls += 1

    def total_ms(self) -> float:
        if not self.enabled:
            return 0.0
        return max((perf_counter() - self._started) * 1000.0, 0.0)

    def stage_timings(self) -> tuple[StageTiming, ...]:
        rows: list[StageTiming] = []
        for stage in sorted(self._stages):
            accumulator = self._stages[stage]
            calls = max(accumulator.calls, 1)
            avg = accumulator.total_ms / calls
            rows.append(
                StageTiming(
                    stage=stage,
                    total_ms=round(accumulator.total_ms, 3),
                    calls=accumulator.calls,
                    avg_ms=round(avg, 3),
                )
            )
        return tuple(rows)

    def payload(self) -> dict[str, object]:
        stage_rows = self.stage_timings()
        return {
            "enabled": self.enabled,
            "total_ms": round(self.total_ms(), 3),
            "stage_timings_ms": {row.stage: row.total_ms for row in stage_rows},
            "stage_calls": {row.stage: row.calls for row in stage_rows},
            "stage_avg_ms": {row.stage: row.avg_ms for row in stage_rows},
        }

    def summary_text(self, *, command: str) -> str:
        payload = self.payload()
        stage_avg = payload["stage_avg_ms"]
        stage_calls = payload["stage_calls"]
        assert isinstance(stage_avg, dict)
        assert isinstance(stage_calls, dict)
        parts: list[str] = []
        for stage in sorted(stage_avg):
            avg = stage_avg[stage]
            calls = stage_calls.get(stage, 0)
            parts.append(f"{stage}={avg}ms({calls}x)")
        compact = ", ".join(parts) if parts else "no_stages"
        return (
            f"profile command={command} total_ms={payload['total_ms']} "
            f"stages=[{compact}]"
        )
