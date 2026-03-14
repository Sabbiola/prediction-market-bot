from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from prediction_market_bot.infrastructure.persistence import JsonlPersistence


def list_run_ids(
    persistence: JsonlPersistence,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit_runs: int = 50,
) -> list[str]:
    start = date_to_start(date_from)
    end = date_to_end(date_to)
    rows = persistence.read_all_artifact_records("pipeline_summaries")

    entries: list[tuple[datetime, str]] = []
    for row in rows:
        run_id = row.get("run_id")
        timestamp = parse_timestamp(row.get("timestamp"))
        if not isinstance(run_id, str) or not run_id.strip():
            continue
        if timestamp is None:
            continue
        if start and timestamp < start:
            continue
        if end and timestamp > end:
            continue
        entries.append((timestamp, run_id))

    entries.sort(key=lambda item: item[0])
    ordered_run_ids: list[str] = []
    seen: set[str] = set()
    for _, run_id in entries:
        if run_id in seen:
            continue
        seen.add(run_id)
        ordered_run_ids.append(run_id)
    if limit_runs > 0:
        return ordered_run_ids[-limit_runs:]
    return ordered_run_ids


def date_to_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def date_to_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
