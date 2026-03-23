import io
import json
import logging

from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.app.settings import LoggingSettings


def test_json_logging_includes_extra_fields() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.handlers = [handler]

    configure_logging(LoggingSettings(level="INFO", json_logs=True))
    root.handlers[0].stream = stream

    logger = logging.getLogger("test.logger")
    logger.info("event happened", extra={"event": "unit_test", "market_id": "m-1"})

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "event happened"
    assert payload["event"] == "unit_test"
    assert payload["market_id"] == "m-1"
