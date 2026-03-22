from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(slots=True, frozen=True)
class TrainingLabLayout:
    dataset_id: str
    root_dir: Path
    runs_dir: Path
    calibration_runs_dir: Path
    model_cards_dir: Path
    latest_training_path: Path
    latest_calibration_path: Path

    @classmethod
    def from_base(cls, *, base_dir: Path, dataset_id: str) -> "TrainingLabLayout":
        root_dir = base_dir / dataset_id / "derived" / "training_lab"
        return cls(
            dataset_id=dataset_id,
            root_dir=root_dir,
            runs_dir=root_dir / "runs",
            calibration_runs_dir=root_dir / "calibration_runs",
            model_cards_dir=root_dir / "model_cards",
            latest_training_path=root_dir / "latest_training_run.json",
            latest_calibration_path=root_dir / "latest_calibration_run.json",
        )

    def ensure_dirs(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_runs_dir.mkdir(parents=True, exist_ok=True)
        self.model_cards_dir.mkdir(parents=True, exist_ok=True)


class TrainingLabStorage:
    def __init__(self, layout: TrainingLabLayout) -> None:
        self.layout = layout
        self.layout.ensure_dirs()

    def new_run_id(self) -> str:
        return f"train-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"

    def new_calibration_id(self) -> str:
        return f"cal-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"

    def run_dir(self, run_id: str) -> Path:
        return self.layout.runs_dir / run_id

    def calibration_dir(self, run_id: str) -> Path:
        return self.layout.calibration_runs_dir / run_id

    def write_json(self, path: Path, payload: Mapping[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
        return path

    def read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
        return {}

    def write_jsonl(self, path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), default=str))
                handle.write("\n")
        return path

    def read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if isinstance(raw, dict):
                    rows.append(raw)
        return rows

    def write_latest_training(self, payload: Mapping[str, Any]) -> Path:
        return self.write_json(self.layout.latest_training_path, payload)

    def write_latest_calibration(self, payload: Mapping[str, Any]) -> Path:
        return self.write_json(self.layout.latest_calibration_path, payload)

    def latest_training_run_id(self) -> str | None:
        latest = self.read_json(self.layout.latest_training_path)
        run_id = str(latest.get("run_id") or "").strip()
        if run_id:
            return run_id
        run_dirs = sorted(path for path in self.layout.runs_dir.glob("train-*") if path.is_dir())
        if not run_dirs:
            return None
        return run_dirs[-1].name

    def latest_calibration_run_id(self) -> str | None:
        latest = self.read_json(self.layout.latest_calibration_path)
        run_id = str(latest.get("run_id") or "").strip()
        if run_id:
            return run_id
        run_dirs = sorted(path for path in self.layout.calibration_runs_dir.glob("cal-*") if path.is_dir())
        if not run_dirs:
            return None
        return run_dirs[-1].name
