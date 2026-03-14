from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.domain.enums import SettlementRequestState, TradeReviewStatus, TxConfirmationStatus
from prediction_market_bot.infrastructure.operational_sqlite import SqliteOperationalRepositories
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.services.paper_portfolio import PaperPortfolioEngine
from prediction_market_bot.services.settlement_requests import SettlementRequestQueueService
from prediction_market_bot.services.trade_review import TradeReviewQueueService


@dataclass(slots=True, frozen=True)
class RuntimeMetricsSnapshot:
    live_source_failures_total: int
    review_queue_depth: int
    open_positions_count: int
    pending_settlements_count: int
    tx_pending_count: int
    tx_mined_count: int
    tx_failed_count: int
    stale_data_events_total: int
    stale_data_blocked_trades_total: int

    def to_dict(self) -> dict[str, int]:
        return {
            "live_source_failures_total": self.live_source_failures_total,
            "review_queue_depth": self.review_queue_depth,
            "open_positions_count": self.open_positions_count,
            "pending_settlements_count": self.pending_settlements_count,
            "tx_pending_count": self.tx_pending_count,
            "tx_mined_count": self.tx_mined_count,
            "tx_failed_count": self.tx_failed_count,
            "stale_data_events_total": self.stale_data_events_total,
            "stale_data_blocked_trades_total": self.stale_data_blocked_trades_total,
        }


def collect_runtime_metrics(
    *,
    settings: AppSettings,
    persistence: JsonlPersistence,
    operational: SqliteOperationalRepositories,
) -> RuntimeMetricsSnapshot:
    del settings
    live_source_failures_total = len(persistence.read_all_artifact_records("source_failures"))
    review_queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    review_queue_depth = len(review_queue.list_queue(status=TradeReviewStatus.PENDING_REVIEW, limit=0))

    portfolio_engine = PaperPortfolioEngine(open_positions_repo=operational.open_positions)
    restored = portfolio_engine.restore_from_repository()
    if not restored:
        portfolio_engine.replay_rows(persistence.read_all_artifact_records("paper_portfolio_events"))
    open_positions_count = portfolio_engine.snapshot().position_count

    settlement_queue = SettlementRequestQueueService(
        persistence,
        pending_repo=operational.pending_settlements,
    )
    pending_settlements_count = len(
        settlement_queue.list_requests(state=SettlementRequestState.PENDING, limit=0)
    )

    attempts = operational.transaction_attempts.list_attempts(limit=0)
    latest_by_intent: dict[str, Mapping[str, object]] = {}
    for attempt in attempts:
        if attempt.intent_id not in latest_by_intent:
            latest_by_intent[attempt.intent_id] = {
                "confirmation_status": attempt.confirmation_status.value,
            }
    tx_pending_count = sum(
        1 for row in latest_by_intent.values() if row.get("confirmation_status") == TxConfirmationStatus.PENDING.value
    )
    tx_mined_count = sum(
        1 for row in latest_by_intent.values() if row.get("confirmation_status") == TxConfirmationStatus.MINED.value
    )
    tx_failed_count = sum(
        1
        for row in latest_by_intent.values()
        if row.get("confirmation_status") in {TxConfirmationStatus.FAILED.value, TxConfirmationStatus.DROPPED.value}
    )
    stale_data_events_total, stale_data_blocked_trades_total = _collect_stale_data_counters(persistence)

    return RuntimeMetricsSnapshot(
        live_source_failures_total=live_source_failures_total,
        review_queue_depth=review_queue_depth,
        open_positions_count=open_positions_count,
        pending_settlements_count=pending_settlements_count,
        tx_pending_count=tx_pending_count,
        tx_mined_count=tx_mined_count,
        tx_failed_count=tx_failed_count,
        stale_data_events_total=stale_data_events_total,
        stale_data_blocked_trades_total=stale_data_blocked_trades_total,
    )


def write_prometheus_textfile(path: str | Path, snapshot: RuntimeMetricsSnapshot) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# HELP pm_bot_live_source_failures_total Total live source failure artifacts recorded.",
        "# TYPE pm_bot_live_source_failures_total gauge",
        f"pm_bot_live_source_failures_total {snapshot.live_source_failures_total}",
        "# HELP pm_bot_review_queue_depth Current pending review queue depth.",
        "# TYPE pm_bot_review_queue_depth gauge",
        f"pm_bot_review_queue_depth {snapshot.review_queue_depth}",
        "# HELP pm_bot_open_positions Current open paper positions.",
        "# TYPE pm_bot_open_positions gauge",
        f"pm_bot_open_positions {snapshot.open_positions_count}",
        "# HELP pm_bot_pending_settlements Current pending settlement requests.",
        "# TYPE pm_bot_pending_settlements gauge",
        f"pm_bot_pending_settlements {snapshot.pending_settlements_count}",
        "# HELP pm_bot_tx_pending Current pending sandbox transactions.",
        "# TYPE pm_bot_tx_pending gauge",
        f"pm_bot_tx_pending {snapshot.tx_pending_count}",
        "# HELP pm_bot_tx_mined Current mined sandbox transactions.",
        "# TYPE pm_bot_tx_mined gauge",
        f"pm_bot_tx_mined {snapshot.tx_mined_count}",
        "# HELP pm_bot_tx_failed Current failed/dropped sandbox transactions.",
        "# TYPE pm_bot_tx_failed gauge",
        f"pm_bot_tx_failed {snapshot.tx_failed_count}",
        "# HELP pm_bot_stale_data_events_total Total stale-data events observed in pipeline counters.",
        "# TYPE pm_bot_stale_data_events_total gauge",
        f"pm_bot_stale_data_events_total {snapshot.stale_data_events_total}",
        "# HELP pm_bot_stale_data_blocked_total Risk decisions blocked due to stale market data.",
        "# TYPE pm_bot_stale_data_blocked_total gauge",
        f"pm_bot_stale_data_blocked_total {snapshot.stale_data_blocked_trades_total}",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def _collect_stale_data_counters(persistence: JsonlPersistence) -> tuple[int, int]:
    stale_events_total = _stale_events_from_pipeline_summaries(
        persistence.read_all_artifact_records("pipeline_summaries")
    )
    stale_blocked_total = _stale_blocked_from_risk_artifacts(
        persistence.read_all_artifact_records("effective_risk_decisions")
        or persistence.read_all_artifact_records("risk_decisions")
    )
    return stale_events_total, stale_blocked_total


def _stale_events_from_pipeline_summaries(rows: Sequence[Mapping[str, Any]]) -> int:
    latest_by_run: dict[str, tuple[datetime | None, Mapping[str, Any]]] = {}
    for row in rows:
        run_id = _to_text(row.get("run_id"))
        payload = row.get("payload")
        if not run_id or not isinstance(payload, Mapping):
            continue
        timestamp = _parse_timestamp(row.get("timestamp"))
        current = latest_by_run.get(run_id)
        if current is None:
            latest_by_run[run_id] = (timestamp, payload)
            continue
        current_ts = current[0]
        if timestamp is None or (current_ts is not None and timestamp <= current_ts):
            continue
        latest_by_run[run_id] = (timestamp, payload)

    total = 0
    for _, payload in latest_by_run.values():
        counters = payload.get("counters")
        if not isinstance(counters, Mapping):
            continue
        total += max(_to_int(counters.get("stale_data_events")), 0)
    return total


def _stale_blocked_from_risk_artifacts(rows: Sequence[Mapping[str, Any]]) -> int:
    latest_by_market: dict[tuple[str, str], tuple[datetime | None, bool]] = {}
    for row in rows:
        run_id = _to_text(row.get("run_id"))
        payload = row.get("payload")
        if not run_id or not isinstance(payload, Mapping):
            continue
        market_id = _to_text(payload.get("market_id"))
        if not market_id:
            continue
        approved = bool(payload.get("approved", False))
        reasons = _to_text_list(payload.get("reasoning"))
        stale_blocked = (not approved) and any("stale_market_data" in reason for reason in reasons)
        key = (run_id, market_id)
        timestamp = _parse_timestamp(row.get("timestamp"))
        current = latest_by_market.get(key)
        if current is None:
            latest_by_market[key] = (timestamp, stale_blocked)
            continue
        current_ts = current[0]
        if timestamp is None or (current_ts is not None and timestamp <= current_ts):
            continue
        latest_by_market[key] = (timestamp, stale_blocked)
    return sum(1 for _, stale_blocked in latest_by_market.values() if stale_blocked)


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _to_text_list(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    text = _to_text(value)
    return [text] if text else []


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return int(float(text))
        except ValueError:
            return 0
    return 0


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
