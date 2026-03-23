from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import monotonic, perf_counter
from typing import Any, Callable, Mapping, TypeVar, cast

from prediction_market_bot.domain.models import TxAttempt
from prediction_market_bot.services import list_run_ids, replay_run
from prediction_market_bot.ui.models import (
    IncidentEventResponse,
    LastRunSummaryResponse,
    RunOptionResponse,
    RunSelectorResponse,
    UiActionLogRowResponse,
)

from .context import UiRuntimeContext
from .shared import (
    event_severity,
    event_summary,
    incident_context,
    incident_links,
    is_noteworthy_event,
    to_text,
)

_T = TypeVar("_T")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UiReadQueryService:
    context: UiRuntimeContext
    _cache_ttl_sec: float = field(init=False, default=0.0)
    _cache: dict[str, tuple[float, object]] = field(init=False, default_factory=dict)
    _slow_query_threshold_ms: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._cache_ttl_sec = max(self.context.settings.performance.ui_poll_cache_ttl_sec, 0.0)
        self._slow_query_threshold_ms = max(self.context.settings.performance.slow_stage_threshold_ms, 0.0)

    def clear_cache(self) -> None:
        self._cache.clear()

    def run_ids(self, *, limit_runs: int = 500) -> tuple[str, ...]:
        cache_key = self._cache_key("run_ids", limit_runs=str(max(limit_runs, 0)))

        def _load() -> tuple[str, ...]:
            rows = self._timed_query(
                query_name="list_run_ids",
                loader=lambda: list_run_ids(self.context.persistence, limit_runs=limit_runs),
            )
            return tuple(rows)

        return self._cached_query(cache_key, _load)

    def run_selector(self, run_id: str | None = None) -> RunSelectorResponse:
        requested_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else ""
        cache_key = self._cache_key("run_selector", run_id=requested_run_id)

        def _load() -> RunSelectorResponse:
            run_ids = self.run_ids(limit_runs=500)
            latest_run_id = run_ids[-1] if run_ids else ""
            selected_run_id = requested_run_id or latest_run_id
            options_list = [RunOptionResponse(run_id=item, label=item) for item in reversed(run_ids[-100:])]
            if requested_run_id and requested_run_id not in run_ids:
                options_list.insert(0, RunOptionResponse(run_id=requested_run_id, label=f"{requested_run_id} (external)"))
            options = tuple(options_list)
            available = bool(run_ids) or bool(requested_run_id)
            if not available:
                note = "no_runs_available"
            elif requested_run_id and requested_run_id not in run_ids:
                note = "run_selector_external_run_id"
            else:
                note = "run_selector_ready"
            return RunSelectorResponse(
                selected_run_id=selected_run_id,
                latest_run_id=latest_run_id,
                options=options,
                available=available,
                note=note,
            )

        return self._cached_query(cache_key, _load)

    def last_run_summary(self, run_id: str) -> LastRunSummaryResponse:
        selected = run_id or self.run_selector().latest_run_id
        if not selected:
            return LastRunSummaryResponse(
                run_id="",
                status="none",
                started_at="",
                finished_at="",
                total_markets=0,
                candidates=0,
                executed=0,
                settled=0,
                wins=0,
                losses=0,
                skipped=0,
                event_count=0,
                available=False,
                note="no_run_available",
            )
        cache_key = self._cache_key("last_run_summary", run_id=selected)

        def _load() -> LastRunSummaryResponse:
            summary = self._timed_query(
                query_name="replay_run",
                loader=lambda: replay_run(self.context.persistence, selected),
                run_id=selected,
            )
            has_data = not (summary.event_count == 0 and summary.total_markets == 0 and summary.candidates == 0)
            return LastRunSummaryResponse(
                run_id=summary.run_id,
                status="ok" if has_data else "missing",
                started_at=summary.started_at.isoformat() if summary.started_at else "",
                finished_at=summary.finished_at.isoformat() if summary.finished_at else "",
                total_markets=summary.total_markets,
                candidates=summary.candidates,
                executed=summary.executed,
                settled=summary.settled,
                wins=summary.wins,
                losses=summary.losses,
                skipped=summary.skipped,
                event_count=summary.event_count,
                available=has_data,
                note="run_summary_loaded" if has_data else "run_summary_empty",
            )

        return self._cached_query(cache_key, _load)

    def artifact_payloads(self, run_id: str, artifact_type: str) -> list[dict[str, Any]]:
        normalized_run_id = run_id.strip()
        normalized_artifact_type = artifact_type.strip()
        if not normalized_run_id or not normalized_artifact_type:
            return []
        cache_key = self._cache_key(
            "artifact_payloads",
            run_id=normalized_run_id,
            artifact_type=normalized_artifact_type,
        )

        def _load() -> tuple[dict[str, Any], ...]:
            rows = self._timed_query(
                query_name="read_artifact_records",
                loader=lambda: self.context.persistence.read_artifact_records(normalized_run_id, normalized_artifact_type),
                run_id=normalized_run_id,
                artifact_type=normalized_artifact_type,
            )
            payloads: list[dict[str, Any]] = []
            for row in rows:
                payload = row.get("payload")
                if isinstance(payload, Mapping):
                    payloads.append(dict(payload))
            return tuple(payloads)

        cached = self._cached_query(cache_key, _load)
        return [dict(item) for item in cached]

    def attempts_from_artifacts(self, run_id: str) -> list[TxAttempt]:
        rows = self.artifact_payloads(run_id, "transaction_attempts")
        attempts: list[TxAttempt] = []
        for payload in rows:
            try:
                attempts.append(TxAttempt.model_validate(payload, strict=False))
            except Exception:
                continue
        return attempts

    def ui_action_logs(
        self,
        *,
        action_prefix: str,
        run_id: str | None,
        limit: int,
    ) -> tuple[UiActionLogRowResponse, ...]:
        records = self.persistence_rows("ui_operator_actions")
        rows: list[UiActionLogRowResponse] = []
        for row in records:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            action = to_text(payload.get("action"))
            if action_prefix and not action.startswith(action_prefix):
                continue
            payload_run_id = to_text(payload.get("run_id"))
            if run_id and payload_run_id and payload_run_id != run_id:
                continue
            rows.append(
                UiActionLogRowResponse(
                    action_id=to_text(payload.get("action_id")),
                    action=action,
                    accepted=bool(payload.get("accepted", False)),
                    status=to_text(payload.get("status")),
                    message=to_text(payload.get("message")),
                    created_at=to_text(payload.get("created_at")),
                    run_id=payload_run_id,
                    queue_id=to_text(payload.get("queue_id")),
                    intent_id=to_text(payload.get("intent_id")),
                    operator_id=to_text(payload.get("operator_id")),
                    acting_user=to_text(payload.get("acting_user")),
                    acting_role=to_text(payload.get("acting_role")),
                )
            )
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(rows[: max(limit, 0)])

    def incident_events(
        self,
        *,
        run_id: str | None,
        limit: int,
    ) -> tuple[IncidentEventResponse, ...]:
        run_filter = run_id.strip() if isinstance(run_id, str) and run_id.strip() else ""
        cache_key = self._cache_key("incident_events", run_id=run_filter, limit=str(max(limit, 0)))

        def _load() -> tuple[IncidentEventResponse, ...]:
            events = self._incident_rows_all()
            if not run_filter:
                return tuple(events[: max(limit, 0)])
            filtered = [row for row in events if row.run_id == run_filter or row.linked_run_id == run_filter]
            return tuple(filtered[: max(limit, 0)])

        return self._cached_query(cache_key, _load)

    def _incident_rows_all(self) -> tuple[IncidentEventResponse, ...]:
        cache_key = self._cache_key("incident_events_all")

        def _load() -> tuple[IncidentEventResponse, ...]:
            events = self._timed_query(
                query_name="read_all_run_events",
                loader=self.context.persistence.read_all_run_events,
            )
            filtered: list[IncidentEventResponse] = []
            for row in events:
                event_run_id = to_text(row.get("run_id"))
                event_type = to_text(row.get("event_type"))
                payload = row.get("payload")
                payload_map = dict(payload) if isinstance(payload, Mapping) else {}
                severity = event_severity(event_type=event_type, payload=payload_map)
                if severity == "info" and not is_noteworthy_event(event_type=event_type, payload=payload_map):
                    continue
                context = incident_context(
                    event_type=event_type,
                    payload=payload_map,
                    event_run_id=event_run_id,
                )
                links = incident_links(
                    linked_run_id=context["linked_run_id"],
                    queue_id=context["queue_id"],
                    intent_id=context["intent_id"],
                    market_id=context["market_id"],
                )
                filtered.append(
                    IncidentEventResponse(
                        timestamp=to_text(row.get("timestamp")),
                        run_id=event_run_id,
                        linked_run_id=context["linked_run_id"],
                        event_type=event_type,
                        severity=severity,
                        summary=event_summary(event_type=event_type, payload=payload_map),
                        component=context["component"],
                        affected_target=context["affected_target"],
                        reason_code=context["reason_code"],
                        queue_id=context["queue_id"],
                        intent_id=context["intent_id"],
                        request_id=context["request_id"],
                        market_id=context["market_id"],
                        source=context["source"],
                        run_url=links["run_url"],
                        review_queue_url=links["review_queue_url"],
                        sandbox_tx_url=links["sandbox_tx_url"],
                        position_url=links["position_url"],
                        settlement_url=links["settlement_url"],
                        reports_url=links["reports_url"],
                    )
                )
            filtered.sort(key=lambda item: item.timestamp, reverse=True)
            return tuple(filtered)

        return self._cached_query(cache_key, _load)

    def persistence_rows(self, artifact_type: str) -> list[dict[str, Any]]:
        normalized_artifact_type = artifact_type.strip()
        if not normalized_artifact_type:
            return []
        cache_key = self._cache_key("persistence_rows", artifact_type=normalized_artifact_type)

        def _load() -> tuple[dict[str, Any], ...]:
            rows = self._timed_query(
                query_name="read_all_artifact_records",
                loader=lambda: self.context.persistence.read_all_artifact_records(normalized_artifact_type),
                artifact_type=normalized_artifact_type,
            )
            return tuple(dict(row) for row in rows if isinstance(row, Mapping))

        cached = self._cached_query(cache_key, _load)
        return [dict(item) for item in cached]

    def _cache_key(self, name: str, **parts: str) -> str:
        tokens = [name.strip().lower()]
        for key in sorted(parts):
            tokens.append(f"{key.strip().lower()}={parts[key].strip()}")
        return "|".join(tokens)

    def _cached_query(self, cache_key: str, loader: Callable[[], _T]) -> _T:
        if self._cache_ttl_sec <= 0:
            return loader()
        now = monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] >= now:
            return cast(_T, cached[1])
        payload = loader()
        self._cache[cache_key] = (now + self._cache_ttl_sec, payload)
        return payload

    def _timed_query(self, query_name: str, loader: Callable[[], _T], **context: str) -> _T:
        started = perf_counter()
        payload = loader()
        duration_ms = max((perf_counter() - started) * 1000.0, 0.0)
        if duration_ms >= self._slow_query_threshold_ms and self._slow_query_threshold_ms > 0:
            logger.warning(
                "ui_read_query_slow",
                extra={
                    "event": "ui_read_query_slow",
                    "query_name": query_name,
                    "duration_ms": round(duration_ms, 3),
                    "threshold_ms": round(self._slow_query_threshold_ms, 3),
                    **context,
                },
            )
        elif duration_ms >= 100:
            logger.info(
                "ui_read_query_timing",
                extra={
                    "event": "ui_read_query_timing",
                    "query_name": query_name,
                    "duration_ms": round(duration_ms, 3),
                    **context,
                },
            )
        return payload
