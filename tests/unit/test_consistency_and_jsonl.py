"""Tests for artifact/operational consistency checker (REC-07) and
JSONL corruption visibility (REC-10).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.services.consistency import (
    ConsistencyChecker,
    ConsistencyReport,
)


# ---------------------------------------------------------------------------
# REC-10: JSONL corruption visibility
# ---------------------------------------------------------------------------


def test_read_jsonl_logs_warning_for_corrupt_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    audit = tmp_path / "audit" / "events.jsonl"
    artifacts = tmp_path / "artifacts"
    store = JsonlPersistence(artifacts_dir=artifacts, audit_log_path=audit)

    bad_file = artifacts / "pipeline_summaries.jsonl"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text(
        '{"run_id": "good-1", "artifact_type": "pipeline_summaries", "payload": {}}\n'
        "THIS IS NOT JSON\n"
        '{"run_id": "good-2", "artifact_type": "pipeline_summaries", "payload": {}}\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="prediction_market_bot.infrastructure.persistence"):
        rows = store.read_all_artifact_records("pipeline_summaries")

    # Only valid rows returned — corrupt line is skipped.
    assert len(rows) == 2
    assert rows[0]["run_id"] == "good-1"
    assert rows[1]["run_id"] == "good-2"

    # Warning must be emitted naming the corrupt line.
    assert any("jsonl_corrupt_line" in r.message for r in caplog.records), caplog.text
    # Error summary must also be emitted.
    assert any("jsonl_read_complete_with_corruption" in r.message for r in caplog.records), caplog.text


def test_read_jsonl_no_warning_when_all_lines_valid(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    audit = tmp_path / "audit" / "events.jsonl"
    artifacts = tmp_path / "artifacts"
    store = JsonlPersistence(artifacts_dir=artifacts, audit_log_path=audit)

    good_file = artifacts / "pipeline_summaries.jsonl"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text(
        '{"run_id": "r1", "payload": {}}\n'
        '{"run_id": "r2", "payload": {}}\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="prediction_market_bot.infrastructure.persistence"):
        rows = store.read_all_artifact_records("pipeline_summaries")

    assert len(rows) == 2
    assert not any("corrupt" in r.message for r in caplog.records)


def test_read_jsonl_returns_empty_for_nonexistent_file(tmp_path: Path) -> None:
    audit = tmp_path / "audit" / "events.jsonl"
    artifacts = tmp_path / "artifacts"
    store = JsonlPersistence(artifacts_dir=artifacts, audit_log_path=audit)
    rows = store.read_all_artifact_records("nonexistent_type")
    assert rows == []


def test_read_jsonl_handles_blank_lines_gracefully(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    audit = tmp_path / "audit" / "events.jsonl"
    artifacts = tmp_path / "artifacts"
    store = JsonlPersistence(artifacts_dir=artifacts, audit_log_path=audit)

    mixed = artifacts / "pipeline_summaries.jsonl"
    mixed.parent.mkdir(parents=True, exist_ok=True)
    mixed.write_text(
        "\n"
        '{"run_id": "r1", "payload": {}}\n'
        "\n\n"
        '{"run_id": "r2", "payload": {}}\n',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="prediction_market_bot.infrastructure.persistence"):
        rows = store.read_all_artifact_records("pipeline_summaries")

    assert len(rows) == 2
    assert not any("corrupt" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# REC-07: consistency checker
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, run_ids: list[str], artifact_type: str = "pipeline_summaries") -> JsonlPersistence:
    audit = tmp_path / "audit" / "events.jsonl"
    artifacts = tmp_path / "artifacts"
    store = JsonlPersistence(artifacts_dir=artifacts, audit_log_path=audit)
    for rid in run_ids:
        store.write_artifact(rid, artifact_type, {"status": "ok", "run_id": rid})
    return store


def test_consistency_checker_reports_ok_with_no_repos(tmp_path: Path) -> None:
    store = _make_store(tmp_path, ["r1", "r2"])
    checker = ConsistencyChecker(persistence=store)
    report = checker.run(recent_n=100)
    assert isinstance(report, ConsistencyReport)
    # No run_repo → run_parity check is skipped (info), not an error.
    assert report.ok
    parity = [f for f in report.findings if f.check == "run_parity"]
    assert parity and parity[0].severity == "info"
    assert "skipped" in parity[0].detail


def test_consistency_checker_jsonl_readable_info_findings(tmp_path: Path) -> None:
    store = _make_store(tmp_path, ["r1"])
    checker = ConsistencyChecker(persistence=store)
    report = checker.run(recent_n=50)

    readable = [f for f in report.findings if f.check.startswith("jsonl_readable_")]
    assert readable, "expected jsonl_readable_* findings"
    # All info-level (files exist or don't exist but are readable)
    assert all(f.severity == "info" for f in readable)


def test_consistency_checker_surfaces_jsonl_read_error(tmp_path: Path) -> None:
    """If the persistence object raises on read, findings contain an error."""

    class _BrokenPersistence:
        def read_all_artifact_records(self, _artifact_type: str) -> list:
            raise OSError("disk full")

        def read_all_run_events(self) -> list:
            raise OSError("disk full")

    checker = ConsistencyChecker(persistence=_BrokenPersistence())
    report = checker.run(recent_n=50)
    error_findings = [f for f in report.findings if f.severity == "error"]
    assert error_findings, "expected at least one error finding"
    assert not report.ok


def test_consistency_report_to_dict_shape(tmp_path: Path) -> None:
    store = _make_store(tmp_path, ["r1"])
    checker = ConsistencyChecker(persistence=store)
    report = checker.run(recent_n=10)
    d = report.to_dict()
    assert isinstance(d["ok"], bool)
    assert isinstance(d["error_count"], int)
    assert isinstance(d["warning_count"], int)
    assert isinstance(d["findings"], list)
    for f in d["findings"]:
        assert "check" in f
        assert "severity" in f
        assert "detail" in f
        assert "affected_ids" in f
