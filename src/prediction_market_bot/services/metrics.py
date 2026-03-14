from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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

    def to_dict(self) -> dict[str, int]:
        return {
            "live_source_failures_total": self.live_source_failures_total,
            "review_queue_depth": self.review_queue_depth,
            "open_positions_count": self.open_positions_count,
            "pending_settlements_count": self.pending_settlements_count,
            "tx_pending_count": self.tx_pending_count,
            "tx_mined_count": self.tx_mined_count,
            "tx_failed_count": self.tx_failed_count,
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

    return RuntimeMetricsSnapshot(
        live_source_failures_total=live_source_failures_total,
        review_queue_depth=review_queue_depth,
        open_positions_count=open_positions_count,
        pending_settlements_count=pending_settlements_count,
        tx_pending_count=tx_pending_count,
        tx_mined_count=tx_mined_count,
        tx_failed_count=tx_failed_count,
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
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
