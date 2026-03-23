from __future__ import annotations

import sqlite3
from pathlib import Path

from prediction_market_bot.domain.enums import ExecutionMode, ExecutionStatus, OutcomeSide, ResolutionStatus, SettlementRequestState
from prediction_market_bot.domain.models import PendingSettlementRequest
from prediction_market_bot.infrastructure.operational_sqlite import SqliteOperationalRepositories


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def test_operational_sqlite_bootstrap_creates_required_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    SqliteOperationalRepositories.bootstrap(db_path)

    tables = _table_names(db_path)
    assert "runs" in tables
    assert "review_queue" in tables
    assert "review_decisions" in tables
    assert "open_positions_state" in tables
    assert "pending_settlements" in tables
    assert "transaction_intents" in tables
    assert "transaction_attempts" in tables
    assert "transaction_receipts" in tables
    assert "operator_control_state" in tables


def test_pending_settlement_repository_persists_across_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    repos_a = SqliteOperationalRepositories.bootstrap(db_path)
    request = PendingSettlementRequest(
        request_id="run-1:market-1:YES:order-1",
        run_id="run-1",
        market_id="market-1",
        execution_mode=ExecutionMode.PAPER,
        execution_status=ExecutionStatus.FILLED,
        side=OutcomeSide.YES,
        stake_usd=100.0,
        fill_price=0.5,
        order_id="order-1",
        state=SettlementRequestState.PENDING,
        resolution_status=ResolutionStatus.PENDING,
        resolution_reason="awaiting_resolution",
        resolved_yes=None,
    )
    repos_a.pending_settlements.upsert_request(request)

    repos_b = SqliteOperationalRepositories.bootstrap(db_path)
    loaded = repos_b.pending_settlements.get_request(request.request_id)
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.state is SettlementRequestState.PENDING
