from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(slots=True, frozen=True)
class FeatureColumn:
    name: str
    dtype: str
    group: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "group": self.group,
            "description": self.description,
        }


@dataclass(slots=True, frozen=True)
class FeatureSchema:
    version: str
    columns: tuple[FeatureColumn, ...]

    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "columns": [column.to_dict() for column in self.columns],
        }


@dataclass(slots=True, frozen=True)
class FeatureBuildSummary:
    dataset_id: str
    corpus_id: str
    schema_version: str
    rows_path: Path
    schema_path: Path
    manifest_path: Path
    rows_written: int
    skipped_missing_decision_timestamp: int
    skipped_unresolved_or_ambiguous: int
    duplicate_rows: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "corpus_id": self.corpus_id,
            "schema_version": self.schema_version,
            "rows_path": str(self.rows_path),
            "schema_path": str(self.schema_path),
            "manifest_path": str(self.manifest_path),
            "rows_written": self.rows_written,
            "skipped_missing_decision_timestamp": self.skipped_missing_decision_timestamp,
            "skipped_unresolved_or_ambiguous": self.skipped_unresolved_or_ambiguous,
            "duplicate_rows": self.duplicate_rows,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True, frozen=True)
class FeatureSchemaInspection:
    dataset_id: str
    schema_version: str
    schema_path: Path
    rows_path: Path
    manifest_path: Path
    schema_exists: bool
    rows_exist: bool
    rows_count: int
    columns: tuple[FeatureColumn, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "schema_version": self.schema_version,
            "schema_path": str(self.schema_path),
            "rows_path": str(self.rows_path),
            "manifest_path": str(self.manifest_path),
            "schema_exists": self.schema_exists,
            "rows_exist": self.rows_exist,
            "rows_count": self.rows_count,
            "columns": [column.to_dict() for column in self.columns],
        }


@dataclass(slots=True, frozen=True)
class FeatureParityVerification:
    dataset_id: str
    schema_version: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "schema_version": self.schema_version,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }
