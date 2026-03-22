from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(slots=True, frozen=True)
class AltFeatureDatasetLayout:
    dataset_id: str
    schema_version: str
    dataset_root: Path
    feature_root: Path
    rows_path: Path
    schema_path: Path
    manifest_path: Path

    @classmethod
    def from_base(
        cls,
        *,
        base_dir: Path,
        dataset_id: str,
        schema_version: str,
    ) -> "AltFeatureDatasetLayout":
        dataset_root = base_dir / dataset_id
        feature_root = dataset_root / "derived" / "alt_feature_store" / schema_version
        return cls(
            dataset_id=dataset_id,
            schema_version=schema_version,
            dataset_root=dataset_root,
            feature_root=feature_root,
            rows_path=feature_root / "alt_feature_rows.jsonl",
            schema_path=feature_root / "alt_feature_schema.json",
            manifest_path=feature_root / "alt_feature_manifest.json",
        )

    def ensure_dirs(self) -> None:
        self.feature_root.mkdir(parents=True, exist_ok=True)


class AltFeatureDatasetStorage:
    def __init__(self, layout: AltFeatureDatasetLayout) -> None:
        self.layout = layout
        self.layout.ensure_dirs()

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> Path:
        self.layout.rows_path.parent.mkdir(parents=True, exist_ok=True)
        with self.layout.rows_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), default=str))
                handle.write("\n")
        return self.layout.rows_path

    def read_rows(self) -> list[dict[str, Any]]:
        if not self.layout.rows_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.layout.rows_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if isinstance(raw, dict):
                    rows.append(raw)
        return rows

    def row_count(self) -> int:
        if not self.layout.rows_path.exists():
            return 0
        count = 0
        with self.layout.rows_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
        return count

    def write_schema(self, payload: Mapping[str, Any]) -> Path:
        self.layout.schema_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.schema_path.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
        return self.layout.schema_path

    def read_schema(self) -> dict[str, Any]:
        if not self.layout.schema_path.exists():
            return {}
        raw = json.loads(self.layout.schema_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
        return {}

    def write_manifest(self, payload: Mapping[str, Any]) -> Path:
        self.layout.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.manifest_path.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
        return self.layout.manifest_path
