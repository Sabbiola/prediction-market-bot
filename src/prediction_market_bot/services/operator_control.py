from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.interfaces import OperatorControlStateRepositoryPort

@dataclass(slots=True)
class OperatorControlState:
    paused: bool = False
    pause_reason: str = ""
    updated_at: str = ""
    last_run_id: str = ""
    last_run_status: str = "none"
    last_run_started_at: str = ""
    last_run_finished_at: str = ""
    last_error: str = ""
    last_report_markdown_path: str = ""
    last_report_json_path: str = ""
    scheduler_last_started_at: str = ""
    scheduler_last_tick_at: str = ""
    scheduler_iterations: int = 0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperatorControlState":
        return cls(
            paused=bool(payload.get("paused", False)),
            pause_reason=_to_text(payload.get("pause_reason")),
            updated_at=_to_text(payload.get("updated_at")),
            last_run_id=_to_text(payload.get("last_run_id")),
            last_run_status=_to_text(payload.get("last_run_status")) or "none",
            last_run_started_at=_to_text(payload.get("last_run_started_at")),
            last_run_finished_at=_to_text(payload.get("last_run_finished_at")),
            last_error=_to_text(payload.get("last_error")),
            last_report_markdown_path=_to_text(payload.get("last_report_markdown_path")),
            last_report_json_path=_to_text(payload.get("last_report_json_path")),
            scheduler_last_started_at=_to_text(payload.get("scheduler_last_started_at")),
            scheduler_last_tick_at=_to_text(payload.get("scheduler_last_tick_at")),
            scheduler_iterations=_to_int(payload.get("scheduler_iterations")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "updated_at": self.updated_at,
            "last_run_id": self.last_run_id,
            "last_run_status": self.last_run_status,
            "last_run_started_at": self.last_run_started_at,
            "last_run_finished_at": self.last_run_finished_at,
            "last_error": self.last_error,
            "last_report_markdown_path": self.last_report_markdown_path,
            "last_report_json_path": self.last_report_json_path,
            "scheduler_last_started_at": self.scheduler_last_started_at,
            "scheduler_last_tick_at": self.scheduler_last_tick_at,
            "scheduler_iterations": self.scheduler_iterations,
        }


def operator_state_path(artifacts_dir: str | Path) -> Path:
    return Path(artifacts_dir) / "operator" / "control_state.json"


def load_operator_state(
    path: Path,
    *,
    repository: OperatorControlStateRepositoryPort | None = None,
) -> OperatorControlState:
    if repository is not None:
        try:
            payload = repository.load_state()
        except Exception:
            payload = None
        if isinstance(payload, Mapping):
            return OperatorControlState.from_dict(payload)
    if not path.exists():
        return OperatorControlState(updated_at=_utc_now())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return OperatorControlState(updated_at=_utc_now())
    if not isinstance(raw, Mapping):
        return OperatorControlState(updated_at=_utc_now())
    return OperatorControlState.from_dict(raw)


def save_operator_state(
    path: Path,
    state: OperatorControlState,
    *,
    repository: OperatorControlStateRepositoryPort | None = None,
) -> None:
    state.updated_at = _utc_now()
    if repository is not None:
        try:
            repository.save_state(state.to_dict())
        except Exception:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0
    return 0
