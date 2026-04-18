"""Artifact/operational consistency checker (REC-07).

The bot uses a **dual-write** pattern: every pipeline run writes to
  1. JSONL artifact files (``JsonlPersistence``) — the source-of-truth audit
     trail and replay corpus.
  2. SQLite / Postgres operational DB (``OperationalRepositories``) — the
     operational read-model for the CLI and UI.

A crash or bug that interrupts one path leaves the two stores in an
inconsistent state — e.g. a run_id present in JSONL but missing from the
operational ``pipeline_runs`` table, or a transaction_receipt in the DB that
has no corresponding JSONL artifact.

This service performs read-only consistency checks that can be run on startup
(``validate-startup`` command), before dress rehearsal, or on demand via
``live-readiness-check``.  It never mutates any store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ConsistencyFinding:
    """A single inconsistency detected between JSONL artifacts and the DB."""

    check: str          # machine-readable check name
    severity: str       # "error" | "warning" | "info"
    detail: str         # human-readable description
    affected_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "severity": self.severity,
            "detail": self.detail,
            "affected_ids": list(self.affected_ids),
        }


@dataclass(slots=True)
class ConsistencyReport:
    """Aggregated result of all consistency checks."""

    findings: list[ConsistencyFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no error-severity findings were detected."""
        return not any(f.severity == "error" for f in self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "findings": [f.to_dict() for f in self.findings],
        }


class ConsistencyChecker:
    """Read-only consistency checker for JSONL ↔ operational-DB parity.

    Usage::

        checker = ConsistencyChecker(persistence=persistence, run_repo=run_repo)
        report  = checker.run(recent_n=200)
        if not report.ok:
            logger.error("consistency_check_failed findings=%s", report.to_dict())
    """

    def __init__(
        self,
        *,
        persistence: object,
        run_repo: object | None = None,
        receipt_repo: object | None = None,
    ) -> None:
        """
        Args:
            persistence:  ``JsonlPersistence`` instance (must expose
                          ``read_all_artifact_records(artifact_type)``).
            run_repo:     ``RunsRepositoryPort`` — if None the run-parity
                          check is skipped.
            receipt_repo: ``TransactionReceiptsRepositoryPort`` — if None the
                          receipt-parity check is skipped.
        """
        self._persistence = persistence
        self._run_repo = run_repo
        self._receipt_repo = receipt_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, *, recent_n: int = 200) -> ConsistencyReport:
        """Execute all registered checks and return an aggregated report.

        Args:
            recent_n: Only inspect the most recent *recent_n* JSONL records
                      per artifact type to bound execution time.  Pass 0 for
                      all records (may be slow on large files).
        """
        report = ConsistencyReport()
        self._check_jsonl_readable(report)
        self._check_run_parity(report, recent_n=recent_n)
        self._check_receipt_parity(report, recent_n=recent_n)
        self._check_audit_log_readable(report)
        _log_report(report)
        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_jsonl_readable(self, report: ConsistencyReport) -> None:
        """Verify that core JSONL artifact files are readable."""
        artifact_types = [
            "pipeline_summaries",
            "prediction_results",
            "risk_decisions",
            "execution_results",
        ]
        for atype in artifact_types:
            try:
                rows = self._persistence.read_all_artifact_records(atype)  # type: ignore[union-attr]
                report.findings.append(
                    ConsistencyFinding(
                        check=f"jsonl_readable_{atype}",
                        severity="info",
                        detail=f"artifact_type={atype} row_count={len(rows)}",
                    )
                )
            except Exception as exc:
                report.findings.append(
                    ConsistencyFinding(
                        check=f"jsonl_readable_{atype}",
                        severity="error",
                        detail=f"artifact_type={atype} read_error={exc}",
                    )
                )

    def _check_audit_log_readable(self, report: ConsistencyReport) -> None:
        """Verify the audit event log is readable."""
        try:
            rows = self._persistence.read_all_run_events()  # type: ignore[union-attr]
            report.findings.append(
                ConsistencyFinding(
                    check="audit_log_readable",
                    severity="info",
                    detail=f"audit_event_count={len(rows)}",
                )
            )
        except Exception as exc:
            report.findings.append(
                ConsistencyFinding(
                    check="audit_log_readable",
                    severity="error",
                    detail=f"audit_log_read_error={exc}",
                )
            )

    def _check_run_parity(self, report: ConsistencyReport, *, recent_n: int) -> None:
        """Check that recent JSONL pipeline_summaries have matching DB run rows."""
        if self._run_repo is None:
            report.findings.append(
                ConsistencyFinding(
                    check="run_parity",
                    severity="info",
                    detail="skipped_no_run_repo",
                )
            )
            return

        try:
            jsonl_rows: list[dict[str, Any]] = self._persistence.read_all_artifact_records(  # type: ignore[union-attr]
                "pipeline_summaries"
            )
        except Exception as exc:
            report.findings.append(
                ConsistencyFinding(
                    check="run_parity",
                    severity="error",
                    detail=f"jsonl_read_failed error={exc}",
                )
            )
            return

        # Collect the most-recent N run_ids from JSONL.
        recent = jsonl_rows[-recent_n:] if recent_n > 0 else jsonl_rows
        jsonl_run_ids: set[str] = set()
        for row in recent:
            rid = str(row.get("run_id") or row.get("payload", {}).get("run_id", "")).strip()
            if rid:
                jsonl_run_ids.add(rid)

        if not jsonl_run_ids:
            report.findings.append(
                ConsistencyFinding(
                    check="run_parity",
                    severity="info",
                    detail="no_jsonl_runs_found skip_parity",
                )
            )
            return

        # Probe the DB for each run_id.
        missing_in_db: list[str] = []
        for rid in sorted(jsonl_run_ids):
            try:
                rows = self._run_repo.get_runs(limit=1, run_id_prefix=rid)  # type: ignore[union-attr]
                if not rows:
                    missing_in_db.append(rid)
            except AttributeError:
                # RunsRepositoryPort may not expose get_runs on all adapters —
                # fall back to a weaker existence check via list_runs if present.
                try:
                    all_runs = self._run_repo.list_runs(limit=recent_n or 200)  # type: ignore[union-attr]
                    db_ids = {str(r.get("run_id", "")) for r in all_runs}
                    missing_in_db = [r for r in jsonl_run_ids if r not in db_ids]
                except Exception:
                    report.findings.append(
                        ConsistencyFinding(
                            check="run_parity",
                            severity="warning",
                            detail="db_query_method_not_available skipped_parity",
                        )
                    )
                    return
                break
            except Exception as exc:
                report.findings.append(
                    ConsistencyFinding(
                        check="run_parity",
                        severity="warning",
                        detail=f"db_query_failed error={exc} skipped_remaining",
                    )
                )
                return

        if missing_in_db:
            report.findings.append(
                ConsistencyFinding(
                    check="run_parity",
                    severity="warning",
                    detail=(
                        f"jsonl_runs_missing_in_db count={len(missing_in_db)} "
                        f"sample={missing_in_db[:5]}"
                    ),
                    affected_ids=tuple(missing_in_db[:20]),
                )
            )
        else:
            report.findings.append(
                ConsistencyFinding(
                    check="run_parity",
                    severity="info",
                    detail=f"all_recent_jsonl_runs_present_in_db checked={len(jsonl_run_ids)}",
                )
            )

    def _check_receipt_parity(self, report: ConsistencyReport, *, recent_n: int) -> None:
        """Check that JSONL execution_results and DB transaction_receipts agree on run_ids."""
        if self._receipt_repo is None:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="info",
                    detail="skipped_no_receipt_repo",
                )
            )
            return

        try:
            jsonl_rows: list[dict[str, Any]] = self._persistence.read_all_artifact_records(  # type: ignore[union-attr]
                "execution_results"
            )
        except Exception as exc:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="error",
                    detail=f"jsonl_read_failed error={exc}",
                )
            )
            return

        recent = jsonl_rows[-recent_n:] if recent_n > 0 else jsonl_rows
        jsonl_run_ids: set[str] = set()
        for row in recent:
            rid = str(row.get("run_id") or row.get("payload", {}).get("run_id", "")).strip()
            if rid:
                jsonl_run_ids.add(rid)

        if not jsonl_run_ids:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="info",
                    detail="no_jsonl_execution_results_found skip_parity",
                )
            )
            return

        # Probe DB receipts.
        try:
            db_receipts = self._receipt_repo.list_receipts(limit=recent_n or 200)  # type: ignore[union-attr]
            db_run_ids = {str(r.get("run_id", "")) for r in db_receipts}
        except Exception as exc:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="warning",
                    detail=f"db_receipt_query_failed error={exc}",
                )
            )
            return

        only_in_jsonl = jsonl_run_ids - db_run_ids
        if only_in_jsonl:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="warning",
                    detail=(
                        f"execution_results_in_jsonl_without_db_receipt "
                        f"count={len(only_in_jsonl)} sample={sorted(only_in_jsonl)[:5]}"
                    ),
                    affected_ids=tuple(sorted(only_in_jsonl)[:20]),
                )
            )
        else:
            report.findings.append(
                ConsistencyFinding(
                    check="receipt_parity",
                    severity="info",
                    detail=f"receipt_parity_ok checked_jsonl_run_ids={len(jsonl_run_ids)}",
                )
            )


def _log_report(report: ConsistencyReport) -> None:
    level = logging.ERROR if not report.ok else logging.INFO
    logger.log(
        level,
        "consistency_check_complete ok=%s errors=%d warnings=%d",
        report.ok,
        report.error_count,
        report.warning_count,
    )
    for finding in report.findings:
        if finding.severity == "error":
            logger.error("consistency_finding check=%s detail=%s", finding.check, finding.detail)
        elif finding.severity == "warning":
            logger.warning("consistency_finding check=%s detail=%s", finding.check, finding.detail)
