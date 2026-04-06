from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Protocol, Sequence
from urllib import error, request

from prediction_market_bot.app.secrets import SecretProvider, build_secret_provider
from prediction_market_bot.app.settings import AppSettings

logger = logging.getLogger(__name__)

AlertSeverity = Literal["info", "warning", "error", "critical"]

_SECRET_TOKENS = ("secret", "token", "password", "apikey", "api_key", "private_key", "authorization")


@dataclass(slots=True, frozen=True)
class AlertEvent:
    event_type: str
    severity: AlertSeverity
    title: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AlertSink(Protocol):
    def send(self, event: AlertEvent) -> None:
        ...


@dataclass(slots=True)
class NoopAlertSink:
    def send(self, event: AlertEvent) -> None:
        del event


@dataclass(slots=True)
class WebhookAlertSink:
    webhook_url: str
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5

    def send(self, event: AlertEvent) -> None:
        payload = {
            "text": f"[{event.severity.upper()}] {event.title} | {event.message}",
            "event_type": event.event_type,
            "severity": event.severity,
            "title": event.title,
            "message": event.message,
            "occurred_at": event.occurred_at,
            "details": _redact_payload(event.details),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        retries = max(self.max_retries, 0)
        for attempt in range(retries + 1):
            try:
                req = request.Request(self.webhook_url, method="POST", data=body, headers=headers)
                with request.urlopen(req, timeout=max(self.timeout_sec, 0.1)) as response:  # nosec B310
                    if int(getattr(response, "status", 200)) >= 400:
                        raise RuntimeError(f"alert_sink_http_{response.status}")
                return
            except (error.URLError, error.HTTPError, TimeoutError, RuntimeError) as exc:
                if attempt >= retries:
                    raise RuntimeError(f"alert_webhook_delivery_failed: {type(exc).__name__}: {exc}") from exc
                delay = max(self.retry_backoff_sec, 0.0) * (attempt + 1)
                if delay > 0:
                    time.sleep(delay)


_SLACK_SEVERITY_EMOJI: dict[str, str] = {
    "info": ":information_source:",
    "warning": ":warning:",
    "error": ":rotating_light:",
    "critical": ":red_circle:",
}

_TELEGRAM_SEVERITY_PREFIX: dict[str, str] = {
    "info": "\u2139\ufe0f",
    "warning": "\u26a0\ufe0f",
    "error": "\U0001f6a8",
    "critical": "\U0001f534",
}


@dataclass(slots=True)
class SlackAlertSink:
    """Sends alerts to a Slack channel via an Incoming Webhook URL."""

    webhook_url: str
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5

    def send(self, event: AlertEvent) -> None:
        emoji = _SLACK_SEVERITY_EMOJI.get(event.severity, ":bell:")
        payload = {
            "text": f"{emoji} *[{event.severity.upper()}]* {event.title}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji} *[{event.severity.upper()}]* {event.title}\n"
                            f"{event.message}"
                        ),
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"*type:* `{event.event_type}` | *at:* {event.occurred_at}"
                            ),
                        }
                    ],
                },
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        retries = max(self.max_retries, 0)
        for attempt in range(retries + 1):
            try:
                req = request.Request(self.webhook_url, method="POST", data=body, headers=headers)
                with request.urlopen(req, timeout=max(self.timeout_sec, 0.1)) as response:  # nosec B310
                    if int(getattr(response, "status", 200)) >= 400:
                        raise RuntimeError(f"slack_sink_http_{response.status}")
                return
            except (error.URLError, error.HTTPError, TimeoutError, RuntimeError) as exc:
                if attempt >= retries:
                    raise RuntimeError(f"slack_webhook_delivery_failed: {type(exc).__name__}: {exc}") from exc
                delay = max(self.retry_backoff_sec, 0.0) * (attempt + 1)
                if delay > 0:
                    time.sleep(delay)


@dataclass(slots=True)
class TelegramAlertSink:
    """Sends alerts to a Telegram chat via the Bot API."""

    bot_token: str
    chat_id: str
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5

    def send(self, event: AlertEvent) -> None:
        prefix = _TELEGRAM_SEVERITY_PREFIX.get(event.severity, "\U0001f514")
        text = (
            f"{prefix} <b>[{event.severity.upper()}]</b> {_escape_html(event.title)}\n"
            f"{_escape_html(event.message)}\n"
            f"<i>type: {_escape_html(event.event_type)} | at: {_escape_html(event.occurred_at)}</i>"
        )
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"  # nosec B105
        retries = max(self.max_retries, 0)
        for attempt in range(retries + 1):
            try:
                req = request.Request(api_url, method="POST", data=body, headers=headers)
                with request.urlopen(req, timeout=max(self.timeout_sec, 0.1)) as response:  # nosec B310
                    if int(getattr(response, "status", 200)) >= 400:
                        raise RuntimeError(f"telegram_sink_http_{response.status}")
                return
            except (error.URLError, error.HTTPError, TimeoutError, RuntimeError) as exc:
                if attempt >= retries:
                    raise RuntimeError(f"telegram_delivery_failed: {type(exc).__name__}: {exc}") from exc
                delay = max(self.retry_backoff_sec, 0.0) * (attempt + 1)
                if delay > 0:
                    time.sleep(delay)


@dataclass(slots=True)
class AlertingService:
    enabled: bool
    sinks: tuple[AlertSink, ...]
    dedupe_window_sec: int = 300
    _last_sent: dict[str, datetime] = field(default_factory=dict)

    def emit(self, event: AlertEvent) -> bool:
        if not self.enabled or not self.sinks:
            return False
        if self._is_duplicate(event):
            return False
        delivered = False
        for sink in self.sinks:
            try:
                sink.send(event)
                delivered = True
            except Exception as exc:
                logger.error(
                    "alert_sink_delivery_failed",
                    extra={
                        "event": "alert_sink_delivery_failed",
                        "alert_event_type": event.event_type,
                        "severity": event.severity,
                        "error": str(exc),
                    },
                )
        if delivered:
            self._remember(event)
        return delivered

    def _is_duplicate(self, event: AlertEvent) -> bool:
        dedupe_key = event.dedupe_key.strip() or f"{event.event_type}:{event.severity}:{event.title}"
        now = datetime.now(UTC)
        last = self._last_sent.get(dedupe_key)
        if last is None:
            return False
        return (now - last).total_seconds() < max(self.dedupe_window_sec, 1)

    def _remember(self, event: AlertEvent) -> None:
        dedupe_key = event.dedupe_key.strip() or f"{event.event_type}:{event.severity}:{event.title}"
        self._last_sent[dedupe_key] = datetime.now(UTC)


def build_alerting_service(settings: AppSettings, *, secrets: SecretProvider | None = None) -> AlertingService:
    if not settings.alerting.enabled:
        return AlertingService(enabled=False, sinks=(), dedupe_window_sec=settings.alerting.dedupe_window_sec)
    secret_provider = secrets if secrets is not None else build_secret_provider(settings)
    sinks: list[AlertSink] = []

    # ── Generic webhook ───────────────────────────────────────────────────────
    webhook = settings.alerting.webhook
    if webhook.enabled:
        url = webhook.webhook_url.strip()
        if not url:
            url = secret_provider.get(webhook.webhook_url_env)
        if url:
            sinks.append(
                WebhookAlertSink(
                    webhook_url=url,
                    timeout_sec=webhook.timeout_sec,
                    max_retries=webhook.max_retries,
                    retry_backoff_sec=webhook.retry_backoff_sec,
                )
            )
        else:
            logger.warning(
                "alerting_webhook_disabled_missing_url",
                extra={"event": "alerting_webhook_disabled_missing_url"},
            )

    # ── Slack ────────────────────────────────────────────────────────────────
    slack = settings.alerting.slack
    if slack.enabled:
        url = slack.webhook_url.strip()
        if not url:
            url = secret_provider.get(slack.webhook_url_env)
        if url:
            sinks.append(
                SlackAlertSink(
                    webhook_url=url,
                    timeout_sec=slack.timeout_sec,
                    max_retries=slack.max_retries,
                    retry_backoff_sec=slack.retry_backoff_sec,
                )
            )
        else:
            logger.warning(
                "alerting_slack_disabled_missing_url",
                extra={"event": "alerting_slack_disabled_missing_url"},
            )

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram = settings.alerting.telegram
    if telegram.enabled:
        token = telegram.bot_token.strip()
        if not token:
            token = secret_provider.get(telegram.bot_token_env)
        chat_id = telegram.chat_id.strip()
        if token and chat_id:
            sinks.append(
                TelegramAlertSink(
                    bot_token=token,
                    chat_id=chat_id,
                    timeout_sec=telegram.timeout_sec,
                    max_retries=telegram.max_retries,
                    retry_backoff_sec=telegram.retry_backoff_sec,
                )
            )
        else:
            logger.warning(
                "alerting_telegram_disabled_missing_token_or_chat",
                extra={"event": "alerting_telegram_disabled_missing_token_or_chat"},
            )

    return AlertingService(
        enabled=bool(sinks),
        sinks=tuple(sinks),
        dedupe_window_sec=settings.alerting.dedupe_window_sec,
    )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, raw in value.items():
            key_text = str(key)
            if any(token in key_text.lower() for token in _SECRET_TOKENS):
                clean[key_text] = "<redacted>"
            else:
                clean[key_text] = _redact_payload(raw)
        return clean
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return value[:400]
    return value
