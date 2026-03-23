from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import UTC, datetime
from typing import Any

from prediction_market_bot.app.settings import LoggingSettings

_STANDARD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: LoggingSettings) -> None:
    handlers: list[logging.Handler] = []
    stream_handler = logging.StreamHandler()
    handlers.append(stream_handler)
    if settings.file_path.strip():
        log_path = Path(settings.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max(settings.rotate_max_bytes, 1),
            backupCount=max(settings.rotate_backup_count, 1),
            encoding="utf-8",
        )
        handlers.append(file_handler)

    formatter: logging.Formatter
    if settings.json_logs:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(settings.level.upper())
