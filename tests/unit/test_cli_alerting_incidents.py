from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot.cli.commands import health_commands, tx_commands
from prediction_market_bot.services.alerting import AlertEvent
from prediction_market_bot.services.metrics import RuntimeMetricsSnapshot
from prediction_market_bot.services.startup import StartupCheck, StartupValidationReport


class _FakeAlertingService:
    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    def emit(self, event: AlertEvent) -> bool:
        self.events.append(event)
        return True


def _failed_startup_report() -> StartupValidationReport:
    return StartupValidationReport(
        checks=(
            StartupCheck(
                name="operational_schema_version",
                ok=False,
                severity="error",
                detail="operational_schema_mismatch current=0 latest=1 pending=1 run=db-upgrade",
            ),
            StartupCheck(
                name="jsonl_persistence",
                ok=True,
                severity="info",
                detail="jsonl_persistence_access_ok",
            ),
        )
    )


def _ok_startup_report() -> StartupValidationReport:
    return StartupValidationReport(
        checks=(
            StartupCheck(
                name="operational_schema_version",
                ok=True,
                severity="info",
                detail="operational_schema_up_to_date version=1",
            ),
        )
    )


def test_validate_startup_emits_alerts_for_failure(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    fake_alerting = _FakeAlertingService()

    monkeypatch.setattr(health_commands, "build_persistence", lambda settings: object())
    monkeypatch.setattr(health_commands, "build_operational_repositories", lambda settings: object())
    monkeypatch.setattr(health_commands, "run_startup_validation", lambda **kwargs: _failed_startup_report())
    monkeypatch.setattr(health_commands, "build_alerting_service", lambda settings: fake_alerting)

    exit_code = health_commands.validate_startup_command(app_cfg, agents_cfg, as_json=True)
    assert exit_code == 1
    assert [event.event_type for event in fake_alerting.events] == [
        "startup_validation_failure",
        "db_migration_mismatch",
    ]


def test_healthcheck_emits_failure_and_repeated_source_alerts(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    fake_alerting = _FakeAlertingService()

    monkeypatch.setattr(health_commands, "build_persistence", lambda settings: object())
    monkeypatch.setattr(health_commands, "build_operational_repositories", lambda settings: object())
    monkeypatch.setattr(health_commands, "run_startup_validation", lambda **kwargs: _failed_startup_report())
    monkeypatch.setattr(health_commands, "build_alerting_service", lambda settings: fake_alerting)
    monkeypatch.setattr(health_commands, "maybe_write_runtime_metrics", lambda **kwargs: None)
    monkeypatch.setattr(
        health_commands,
        "collect_runtime_metrics",
        lambda **kwargs: RuntimeMetricsSnapshot(
            live_source_failures_total=999,
            review_queue_depth=0,
            open_positions_count=0,
            pending_settlements_count=0,
            tx_pending_count=0,
            tx_mined_count=0,
            tx_failed_count=0,
            stale_data_events_total=0,
            stale_data_blocked_trades_total=0,
        ),
    )

    exit_code = health_commands.healthcheck_command(app_cfg, agents_cfg, as_json=True)
    assert exit_code == 1
    assert [event.event_type for event in fake_alerting.events] == [
        "healthcheck_failure",
        "db_migration_mismatch",
        "repeated_live_source_failures",
    ]


def test_tx_reconcile_emits_alert_on_reconcile_failure(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    fake_alerting = _FakeAlertingService()

    class _FailingTxService:
        def reconcile(self, **kwargs: object) -> list[object]:
            raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(tx_commands, "build_persistence", lambda settings: object())
    monkeypatch.setattr(tx_commands, "build_operational_repositories", lambda settings: object())
    monkeypatch.setattr(tx_commands, "run_startup_validation", lambda **kwargs: _ok_startup_report())
    monkeypatch.setattr(tx_commands, "build_alerting_service", lambda settings: fake_alerting)
    monkeypatch.setattr(tx_commands, "require_cli_role", lambda **kwargs: None)
    monkeypatch.setattr(tx_commands, "_build_sandbox_tx_service", lambda settings: _FailingTxService())

    exit_code = tx_commands.tx_reconcile_command(
        app_cfg,
        agents_cfg,
        run_id="run-123",
        intent_id="intent-123",
        limit=10,
        as_json=True,
        acting_user="operator",
        acting_role="operator",
    )
    assert exit_code == 1
    assert [event.event_type for event in fake_alerting.events] == ["tx_reconciliation_failure"]
    details = fake_alerting.events[0].details
    assert details["run_id"] == "run-123"
    assert details["intent_id"] == "intent-123"
