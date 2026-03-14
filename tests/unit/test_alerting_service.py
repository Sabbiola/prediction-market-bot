from __future__ import annotations

import json
from pathlib import Path

import yaml

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.services.alerting import AlertEvent, build_alerting_service


def _enable_alerting(app_cfg: Path) -> None:
    raw = yaml.safe_load(app_cfg.read_text(encoding="utf-8")) or {}
    raw["alerting"] = {
        "enabled": True,
        "dedupe_window_sec": 300,
        "repeated_live_source_failures_threshold": 1,
        "webhook": {
            "enabled": True,
            "webhook_url_env": "PM_BOT_ALERT_WEBHOOK_URL",
            "timeout_sec": 1.0,
            "max_retries": 0,
            "retry_backoff_sec": 0.0,
        },
    }
    app_cfg.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_alerting_service_sends_webhook_and_redacts_sensitive_fields(
    temp_config_paths: tuple[Path, Path],
    monkeypatch: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    _enable_alerting(app_cfg)
    monkeypatch.setenv("PM_BOT_ALERT_WEBHOOK_URL", "https://alerts.example/webhook")
    captured: list[dict[str, object]] = []

    class _FakeResponse:
        status = 200

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            del exc_type, exc, tb
            return False

    def _fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        del timeout
        body = getattr(req, "data", b"")
        captured.append(json.loads(body.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr("prediction_market_bot.services.alerting.request.urlopen", _fake_urlopen)
    settings = load_settings(app_cfg, agents_cfg)
    service = build_alerting_service(settings)

    sent = service.emit(
        AlertEvent(
            event_type="test_event",
            severity="error",
            title="Test",
            message="Alert body",
            details={"api_key": "secret-value", "ok": True},
            dedupe_key="same-key",
        )
    )
    assert sent is True
    assert len(captured) == 1
    assert captured[0]["details"]["api_key"] == "<redacted>"

    sent_duplicate = service.emit(
        AlertEvent(
            event_type="test_event",
            severity="error",
            title="Test",
            message="Alert body",
            details={"api_key": "secret-value", "ok": True},
            dedupe_key="same-key",
        )
    )
    assert sent_duplicate is False
    assert len(captured) == 1


def test_alerting_service_disabled_by_default(temp_config_paths: tuple[Path, Path]) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    service = build_alerting_service(settings)
    sent = service.emit(
        AlertEvent(
            event_type="noop",
            severity="info",
            title="noop",
            message="noop",
        )
    )
    assert sent is False
