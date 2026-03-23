from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

from prediction_market_bot.domain.enums import TradeReviewStatus
from prediction_market_bot.domain.models import TxIntent
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    PositionsRowResponse,
    PositionsTabResponse,
    RunSelectorResponse,
    RuntimeMetricsResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    as_map,
    as_sequence,
    deep_get,
    empty_positions,
    extract_market_id,
    intent_id,
    to_float,
    to_text,
)


def open_positions_count(metrics: RuntimeMetricsResponse) -> int:
    return metrics.open_positions_count


def build_positions_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
    market_id_filter: str | None = None,
) -> PositionsTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_positions(selector, "no_run_selected")

    snapshot = as_map(context.operational.open_positions.load_snapshot())
    if not snapshot:
        snapshot_rows = queries.artifact_payloads(run, "paper_open_positions_state")
        if snapshot_rows:
            snapshot = dict(snapshot_rows[-1])

    position_payloads = as_sequence(snapshot.get("positions"))
    if not position_payloads:
        return empty_positions(selector, "positions_data_missing")

    market_meta = _market_metadata(queries=queries, run_id=run)
    review_links = _review_context(queries=queries, run_id=run)
    intent_links = _intent_context(context=context, queries=queries, run_id=run)

    total_exposure_usd = to_float(snapshot.get("total_exposure_usd"))
    if total_exposure_usd <= 0:
        total_exposure_usd = sum(max(to_float(payload.get("market_value_usd")), 0.0) for payload in position_payloads)
    realized_pnl_usd = to_float(snapshot.get("realized_pnl_usd"))
    unrealized_pnl_usd = to_float(snapshot.get("unrealized_pnl_usd"))
    total_pnl_usd = to_float(snapshot.get("total_pnl_usd"))
    if total_pnl_usd == 0.0 and (realized_pnl_usd != 0.0 or unrealized_pnl_usd != 0.0):
        total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd

    rows: list[PositionsRowResponse] = []
    for payload in position_payloads:
        market_id = to_text(payload.get("market_id"))
        if not market_id:
            continue
        review_queue_id = review_links.queue_id_by_market.get(market_id, "")
        review_status = review_links.status_by_queue.get(review_queue_id, "")
        tx_intent_id = intent_links.intent_id_by_market.get(market_id, "")
        if not review_queue_id:
            review_queue_id = intent_links.queue_id_by_market.get(market_id, "")
        links = _panel_links(
            run_id=run,
            market_id=market_id,
            review_queue_id=review_queue_id,
            tx_intent_id=tx_intent_id,
        )
        market_value_usd = to_float(payload.get("market_value_usd"))
        exposure_pct = (market_value_usd / total_exposure_usd * 100.0) if total_exposure_usd > 0 else 0.0
        metadata = market_meta.get(market_id, {})
        rows.append(
            PositionsRowResponse(
                market_id=market_id,
                market_title=to_text(metadata.get("market_title")),
                market_status=to_text(metadata.get("market_status")) or "OPEN",
                hours_to_resolution=_optional_float(metadata.get("hours_to_resolution")),
                side=to_text(payload.get("side")) or "n/a",
                shares=to_float(payload.get("shares")),
                avg_entry_price=to_float(payload.get("avg_entry_price")),
                cost_basis_usd=to_float(payload.get("cost_basis_usd")),
                mark_price=to_float(payload.get("mark_price")),
                market_value_usd=market_value_usd,
                unrealized_pnl_usd=to_float(payload.get("unrealized_pnl_usd")),
                exposure_pct=exposure_pct,
                review_queue_id=review_queue_id,
                review_status=review_status,
                tx_intent_id=tx_intent_id,
                review_queue_url=links["review_queue_url"],
                prediction_url=links["prediction_url"],
                risk_url=links["risk_url"],
                sandbox_tx_url=links["sandbox_tx_url"],
                settlement_url=links["settlement_url"],
                updated_at=to_text(snapshot.get("as_of")),
            )
        )
    rows.sort(key=lambda item: abs(item.market_value_usd), reverse=True)

    active_market_id = to_text(market_id_filter)
    if active_market_id and active_market_id not in {row.market_id for row in rows}:
        active_market_id = ""

    exposure_distribution = tuple(
        ChartPointResponse(label=row.market_id, value=round(row.market_value_usd, 6))
        for row in rows[:12]
    )
    open_count = int(snapshot.get("open_position_count") or snapshot.get("position_count") or len(rows))
    linked_review_count = sum(1 for row in rows if row.review_queue_id)
    linked_tx_count = sum(1 for row in rows if row.tx_intent_id)

    return PositionsTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=bool(rows),
        note="positions_data_loaded",
        active_market_id=active_market_id,
        open_positions_count=open_count,
        linked_review_count=linked_review_count,
        linked_tx_count=linked_tx_count,
        total_exposure_usd=total_exposure_usd,
        unrealized_pnl_usd=unrealized_pnl_usd,
        realized_pnl_usd=realized_pnl_usd,
        total_pnl_usd=total_pnl_usd,
        exposure_distribution=exposure_distribution,
        rows=tuple(rows[:80]),
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return to_float(value)


def _market_metadata(*, queries: UiReadQueryService, run_id: str) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for row in queries.artifact_payloads(run_id, "market_snapshots"):
        market_id = extract_market_id(row)
        if not market_id:
            continue
        payload[market_id] = {
            "market_title": to_text(deep_get(row, "market", "title")) or to_text(row.get("title")),
            "market_status": to_text(deep_get(row, "market", "status")) or to_text(row.get("status")),
            "hours_to_resolution": row.get("hours_to_resolution"),
        }
    return payload


class _ReviewContext:
    def __init__(self) -> None:
        self.queue_id_by_market: dict[str, str] = {}
        self.status_by_queue: dict[str, str] = {}


def _review_context(*, queries: UiReadQueryService, run_id: str) -> _ReviewContext:
    context = _ReviewContext()
    for row in queries.artifact_payloads(run_id, "trade_review_candidates"):
        market_id = extract_market_id(row)
        queue_id = to_text(row.get("queue_id"))
        if market_id and queue_id and market_id not in context.queue_id_by_market:
            context.queue_id_by_market[market_id] = queue_id
            context.status_by_queue[queue_id] = TradeReviewStatus.PENDING_REVIEW.value

    latest_decision_by_queue: dict[str, dict[str, object]] = {}
    for row in queries.artifact_payloads(run_id, "trade_review_decisions"):
        queue_id = to_text(row.get("queue_id"))
        if not queue_id:
            continue
        current = latest_decision_by_queue.get(queue_id)
        decided_at = to_text(row.get("decided_at"))
        if current is None or decided_at >= to_text(current.get("decided_at")):
            latest_decision_by_queue[queue_id] = row
    for queue_id, decision in latest_decision_by_queue.items():
        status = to_text(decision.get("status_after_action")).upper()
        if status:
            context.status_by_queue[queue_id] = status
    return context


class _IntentContext:
    def __init__(self) -> None:
        self.intent_id_by_market: dict[str, str] = {}
        self.queue_id_by_market: dict[str, str] = {}


def _intent_context(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    run_id: str,
) -> _IntentContext:
    payload = _IntentContext()
    intents = context.operational.transaction_intents.list_intents(run_id=run_id, limit=0)
    for row in intents:
        market_id = row.market_id
        if market_id and market_id not in payload.intent_id_by_market:
            payload.intent_id_by_market[market_id] = intent_id(row)
            payload.queue_id_by_market[market_id] = row.review_queue_id
    if payload.intent_id_by_market:
        return payload

    for raw_payload in queries.artifact_payloads(run_id, "order_intents"):
        try:
            parsed = TxIntent.model_validate(raw_payload, strict=False)
        except Exception:
            continue
        market_id = parsed.market_id
        if market_id and market_id not in payload.intent_id_by_market:
            payload.intent_id_by_market[market_id] = intent_id(parsed)
            payload.queue_id_by_market[market_id] = parsed.review_queue_id
    return payload


def _panel_links(
    *,
    run_id: str,
    market_id: str,
    review_queue_id: str,
    tx_intent_id: str,
) -> dict[str, str]:
    run_token = quote_plus(run_id.strip())
    market_token = quote_plus(market_id.strip())
    root = f"/?run_id={run_token}"
    review_queue_url = f"{root}&active_tab=review-queue&review_status=ALL"
    if review_queue_id:
        review_queue_url = f"{review_queue_url}&review_queue_id={quote_plus(review_queue_id)}"
    sandbox_tx_url = f"{root}&active_tab=sandbox-tx"
    if tx_intent_id:
        sandbox_tx_url = f"{sandbox_tx_url}&tx_intent_id={quote_plus(tx_intent_id)}"
    return {
        "review_queue_url": review_queue_url,
        "prediction_url": f"{root}&active_tab=prediction",
        "risk_url": f"{root}&active_tab=risk",
        "sandbox_tx_url": sandbox_tx_url,
        "settlement_url": f"{root}&active_tab=settlement&position_market_id={market_token}",
    }
