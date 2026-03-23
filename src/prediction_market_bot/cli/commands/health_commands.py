from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.services import AlertEvent, build_alerting_service, collect_runtime_metrics

from prediction_market_bot.cli.common import (
    maybe_write_runtime_metrics,
    print_startup_report,
    run_startup_validation,
)


def validate_startup_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    alerting = build_alerting_service(settings)
    if not report.ok:
        failing = [check.to_dict() for check in report.checks if not check.ok]
        alerting.emit(
            AlertEvent(
                event_type="startup_validation_failure",
                severity="error",
                title="Startup validation failed",
                message="startup checks failed; operator action required",
                details={"failed_checks": failing},
                dedupe_key="startup_validation_failure",
            )
        )
        schema_failures = [check for check in report.checks if check.name == "operational_schema_version" and not check.ok]
        if schema_failures:
            alerting.emit(
                AlertEvent(
                    event_type="db_migration_mismatch",
                    severity="error",
                    title="Operational DB schema mismatch",
                    message="operational schema is not up to date",
                    details={"check": schema_failures[0].to_dict()},
                    dedupe_key="db_migration_mismatch",
                )
            )
    payload = report.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Startup validation: {'OK' if report.ok else 'FAILED'}")
        print_startup_report(report)
    return 0 if report.ok else 1


def healthcheck_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    if not settings.healthcheck.enabled:
        print("Healthcheck is disabled by configuration.")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    report = run_startup_validation(settings=settings, persistence=persistence, operational=operational)
    metrics = maybe_write_runtime_metrics(settings=settings, persistence=persistence, operational=operational)
    metrics_snapshot = metrics or collect_runtime_metrics(
        settings=settings,
        persistence=persistence,
        operational=operational,
    )
    alerting = build_alerting_service(settings)
    if not report.ok:
        failing = [check.to_dict() for check in report.checks if not check.ok]
        alerting.emit(
            AlertEvent(
                event_type="healthcheck_failure",
                severity="error",
                title="Healthcheck failed",
                message="healthcheck returned failed status",
                details={"failed_checks": failing},
                dedupe_key="healthcheck_failure",
            )
        )
        schema_failures = [check for check in report.checks if check.name == "operational_schema_version" and not check.ok]
        if schema_failures:
            alerting.emit(
                AlertEvent(
                    event_type="db_migration_mismatch",
                    severity="error",
                    title="Operational DB schema mismatch",
                    message="operational schema is not up to date",
                    details={"check": schema_failures[0].to_dict()},
                    dedupe_key="db_migration_mismatch",
                )
            )
    if metrics_snapshot.live_source_failures_total >= settings.alerting.repeated_live_source_failures_threshold:
        alerting.emit(
            AlertEvent(
                event_type="repeated_live_source_failures",
                severity="warning",
                title="Repeated live source failures",
                message=(
                    "live source failures exceeded threshold "
                    f"{settings.alerting.repeated_live_source_failures_threshold}"
                ),
                details={
                    "live_source_failures_total": metrics_snapshot.live_source_failures_total,
                    "threshold": settings.alerting.repeated_live_source_failures_threshold,
                },
                dedupe_key="repeated_live_source_failures",
            )
        )
    payload = {
        "status": "ok" if report.ok else "failed",
        "timestamp": datetime.now(UTC).isoformat(),
        "runtime_mode": settings.runtime.mode.value,
        "execution_mode": settings.execution.mode.value,
        "checks": [check.to_dict() for check in report.checks],
        "metrics": metrics_snapshot.to_dict(),
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Healthcheck status: {payload['status']}")
        for check in report.checks:
            state = "OK" if check.ok else "FAIL"
            print(f"- [{state}] {check.name}: {check.detail}")
    return 0 if report.ok else 1
