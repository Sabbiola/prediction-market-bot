from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import OrderIntent
from prediction_market_bot.services import PaperPortfolioEngine


class _InMemoryPersistence:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, Mapping[str, Any]]] = []
        self.artifacts: list[tuple[str, str, Mapping[str, Any]]] = []

    def write_run_event(self, run_id: str, event_type: str, payload: Mapping[str, Any]) -> None:
        self.events.append((run_id, event_type, payload))

    def write_artifact(self, run_id: str, artifact_type: str, payload: Mapping[str, Any]) -> None:
        self.artifacts.append((run_id, artifact_type, payload))


def _order(
    *,
    market_id: str = "market-1",
    side: OutcomeSide = OutcomeSide.YES,
    stake_usd: float = 100.0,
    limit_price: float = 0.5,
) -> OrderIntent:
    return OrderIntent(
        market_id=market_id,
        venue="polymarket",
        side=side,
        stake_usd=stake_usd,
        limit_price=limit_price,
        rationale="unit-test",
    )


def test_paper_portfolio_opens_position_from_simulated_fill() -> None:
    persistence = _InMemoryPersistence()
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    engine = PaperPortfolioEngine(persistence=persistence, now_fn=lambda: now)

    engine.simulate_order_fill(run_id="paper-run-1", order=_order())
    snapshot = engine.snapshot()

    assert snapshot.position_count == 1
    assert snapshot.realized_pnl_usd == 0.0
    assert snapshot.unrealized_pnl_usd == 0.0
    assert snapshot.total_exposure_usd == 100.0
    assert snapshot.market_exposure_usd == {"market-1": 100.0}
    assert snapshot.positions[0].shares == 200.0
    assert snapshot.positions[0].avg_entry_price == 0.5
    assert any(artifact[1] == "paper_portfolio_events" for artifact in persistence.artifacts)
    assert any(artifact[1] == "paper_portfolio_snapshots" for artifact in persistence.artifacts)


def test_paper_portfolio_updates_unrealized_pnl_after_mark() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    engine = PaperPortfolioEngine(now_fn=lambda: now)

    engine.simulate_order_fill(run_id="paper-run-2", order=_order(limit_price=0.5))
    engine.mark_market(run_id="paper-run-2", market_id="market-1", yes_price=0.6)
    snapshot = engine.snapshot()

    assert snapshot.position_count == 1
    assert snapshot.realized_pnl_usd == 0.0
    assert snapshot.unrealized_pnl_usd == 20.0
    assert snapshot.total_pnl_usd == 20.0
    assert snapshot.total_exposure_usd == 120.0
    assert snapshot.market_exposure_usd["market-1"] == 120.0
    assert snapshot.positions[0].mark_price == 0.6


def test_paper_portfolio_settles_position_and_realizes_pnl() -> None:
    now = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
    engine = PaperPortfolioEngine(now_fn=lambda: now)

    engine.simulate_order_fill(run_id="paper-run-3", order=_order(limit_price=0.4))
    engine.mark_market(run_id="paper-run-3", market_id="market-1", yes_price=0.55)
    engine.settle_market(run_id="paper-run-3", market_id="market-1", resolved_yes=True)
    snapshot = engine.snapshot()

    assert snapshot.position_count == 0
    assert snapshot.realized_pnl_usd == 150.0
    assert snapshot.unrealized_pnl_usd == 0.0
    assert snapshot.total_pnl_usd == 150.0
    assert snapshot.total_exposure_usd == 0.0
    assert snapshot.market_exposure_usd == {}
