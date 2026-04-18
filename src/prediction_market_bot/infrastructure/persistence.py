from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from prediction_market_bot.interfaces import PersistencePort

logger = logging.getLogger(__name__)


class JsonlPersistence(PersistencePort):
    """Minimal JSONL persistence adapter for dry-run artifacts."""

    def __init__(self, artifacts_dir: str | Path, audit_log_path: str | Path) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.audit_log_path = Path(audit_log_path)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "event_type": event_type,
            "payload": dict(payload),
        }
        with self._write_lock:
            self._append_jsonl(self.audit_log_path, record)

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        path = self.artifacts_dir / f"{artifact_type}.jsonl"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "artifact_type": artifact_type,
            "payload": dict(payload),
        }
        with self._write_lock:
            self._append_jsonl(path, record)

    def read_run_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._read_jsonl(self.audit_log_path)
        return [row for row in rows if row.get("run_id") == run_id]

    def read_all_run_events(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.audit_log_path)

    def read_artifact_records(self, run_id: str, artifact_type: str) -> list[dict[str, Any]]:
        path = self.artifacts_dir / f"{artifact_type}.jsonl"
        rows = self._read_jsonl(path)
        return [row for row in rows if row.get("run_id") == run_id]

    def read_all_artifact_records(self, artifact_type: str) -> list[dict[str, Any]]:
        path = self.artifacts_dir / f"{artifact_type}.jsonl"
        return self._read_jsonl(path)

    @staticmethod
    def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str))
            handle.write("\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        corrupt_count = 0
        with path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError as exc:
                    corrupt_count += 1
                    logger.warning(
                        "jsonl_corrupt_line path=%s line=%d error=%s preview=%.80r",
                        path,
                        lineno,
                        exc,
                        text,
                    )
                    continue
                if isinstance(raw, dict):
                    rows.append(raw)
        if corrupt_count:
            logger.error(
                "jsonl_read_complete_with_corruption path=%s corrupt_lines=%d valid_rows=%d "
                "hint=file_may_need_manual_repair",
                path,
                corrupt_count,
                len(rows),
            )
        return rows
