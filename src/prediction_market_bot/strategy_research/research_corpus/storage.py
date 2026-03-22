from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import ResearchCorpusCheckpoint

RAW_FILES = ("source_payloads",)
NORMALIZED_FILES = ("market_decisions", "evidence_findings")


@dataclass(slots=True, frozen=True)
class CorpusLayout:
    corpus_id: str
    root_dir: Path
    raw_dir: Path
    normalized_dir: Path
    checkpoints_dir: Path
    checkpoint_path: Path

    @classmethod
    def from_base(cls, *, base_dir: Path, corpus_id: str, checkpoint_path: Path | None = None) -> "CorpusLayout":
        root = base_dir / corpus_id
        checkpoints_dir = root / "checkpoints"
        resolved_checkpoint = checkpoint_path if checkpoint_path is not None else checkpoints_dir / "corpus_checkpoint.json"
        return cls(
            corpus_id=corpus_id,
            root_dir=root,
            raw_dir=root / "raw",
            normalized_dir=root / "normalized",
            checkpoints_dir=checkpoints_dir,
            checkpoint_path=resolved_checkpoint,
        )

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def raw_file(self, name: str) -> Path:
        return self.raw_dir / f"{name}.jsonl"

    def normalized_file(self, name: str) -> Path:
        return self.normalized_dir / f"{name}.jsonl"


class CorpusStorage:
    def __init__(self, layout: CorpusLayout) -> None:
        self.layout = layout
        self.layout.ensure_dirs()

    def append_raw(self, name: str, payload: Mapping[str, Any]) -> None:
        self._append_jsonl(self.layout.raw_file(name), payload)

    def append_normalized(self, name: str, payload: Mapping[str, Any]) -> None:
        self._append_jsonl(self.layout.normalized_file(name), payload)

    def read_rows(self, *, normalized: bool, name: str) -> list[dict[str, Any]]:
        path = self.layout.normalized_file(name) if normalized else self.layout.raw_file(name)
        return self._read_jsonl(path)

    def row_count(self, *, normalized: bool, name: str) -> int:
        path = self.layout.normalized_file(name) if normalized else self.layout.raw_file(name)
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def load_checkpoint(
        self,
        *,
        corpus_id: str,
        source_dataset_id: str,
    ) -> ResearchCorpusCheckpoint:
        if not self.layout.checkpoint_path.exists():
            return ResearchCorpusCheckpoint(corpus_id=corpus_id, source_dataset_id=source_dataset_id)
        with self.layout.checkpoint_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return ResearchCorpusCheckpoint(corpus_id=corpus_id, source_dataset_id=source_dataset_id)
        return ResearchCorpusCheckpoint.from_dict(
            raw,
            default_corpus_id=corpus_id,
            default_source_dataset_id=source_dataset_id,
        )

    def save_checkpoint(self, checkpoint: ResearchCorpusCheckpoint) -> None:
        self.layout.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.checkpoint_path.write_text(json.dumps(checkpoint.to_dict(), indent=2), encoding="utf-8")

    def clear_checkpoint(self) -> None:
        if self.layout.checkpoint_path.exists():
            self.layout.checkpoint_path.unlink()

    def write_manifest(self, payload: Mapping[str, Any]) -> Path:
        path = self.layout.root_dir / "corpus_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(payload), default=str))
            handle.write("\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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
