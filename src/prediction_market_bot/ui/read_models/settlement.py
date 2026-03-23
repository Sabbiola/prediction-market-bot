from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

from prediction_market_bot.ui.models import (
    RunSelectorResponse,
    SettlementPendingRowResponse,
    SettlementRowResponse,
    SettlementTabResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import chart_from_counter, empty_settlement, extract_market_id, intent_id, to_float, to_optional_float, to_text


def build_settlement_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> SettlementTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_settlement(selector, "no_run_selected")

    settlements = queries.artifact_payloads(run, "settlement_results")
    pending_payloads = queries.artifact_payloads(run, "pending_settlement_requests")
    resolution_checks = queries.artifact_payloads(run, "resolution_checks")

    reason_by_market: dict[str, str] = {}
    check_count_by_market: dict[str, int] = {}
    failure_count = 0
    for payload in resolution_checks:
        market_id = extract_market_id(payload)
        if not market_id:
            continue
        reason_by_market[market_id] = to_text(payload.get("reason"))
        check_count_by_market[market_id] = check_count_by_market.get(market_id, 0) + 1
        status = to_text(payload.get("status")).upper()
        if status in {"AMBIGUOUS", "FAILED"}:
            failure_count += 1

    review_queue_by_market, tx_intent_by_market = _review_and_tx_links(context=context, queries=queries, run_id=run)

    outcome_counter: dict[str, int] = {}
    resolved_rows: list[SettlementRowResponse] = []
    realized_pnl_usd = 0.0
    for payload in settlements:
        market_id = extract_market_id(payload)
        outcome = to_text(payload.get("outcome_classification")).upper() or "UNKNOWN"
        outcome_counter[outcome] = outcome_counter.get(outcome, 0) + 1
        resolved_raw = payload.get("resolved_yes")
        resolved_yes = "n/a"
        if isinstance(resolved_raw, bool):
            resolved_yes = "YES" if resolved_raw else "NO"
        pnl_usd = to_float(payload.get("pnl_usd"))
        realized_pnl_usd += pnl_usd
        review_queue_id = review_queue_by_market.get(market_id, "")
        tx_intent_id = tx_intent_by_market.get(market_id, "")
        links = _links(
            run_id=run,
            market_id=market_id,
            review_queue_id=review_queue_id,
            tx_intent_id=tx_intent_id,
        )
        resolved_rows.append(
            SettlementRowResponse(
                request_id=_request_id(run_id=run, payload=payload),
                market_id=market_id or "n/a",
                state="SETTLED",
                resolution_status="RESOLVED",
                outcome_classification=outcome,
                resolved_yes=resolved_yes,
                pnl_usd=pnl_usd,
                execution_side=to_text(payload.get("side")) or "n/a",
                resolution_reason=reason_by_market.get(market_id, ""),
                retry_count=max(check_count_by_market.get(market_id, 1) - 1, 0),
                review_queue_id=review_queue_id,
                tx_intent_id=tx_intent_id,
                review_queue_url=links["review_queue_url"],
                sandbox_tx_url=links["sandbox_tx_url"],
                position_url=links["position_url"],
                updated_at=to_text(payload.get("updated_at")),
            )
        )
    resolved_rows.sort(key=lambda item: abs(item.pnl_usd), reverse=True)

    latest_pending = _latest_pending_requests(pending_payloads)
    pending_rows: list[SettlementPendingRowResponse] = []
    for request_id, payload in latest_pending.items():
        if to_text(payload.get("state")).upper() != "PENDING":
            continue
        market_id = extract_market_id(payload)
        review_queue_id = review_queue_by_market.get(market_id, "")
        tx_intent_id = tx_intent_by_market.get(market_id, "")
        links = _links(
            run_id=run,
            market_id=market_id,
            review_queue_id=review_queue_id,
            tx_intent_id=tx_intent_id,
        )
        pending_rows.append(
            SettlementPendingRowResponse(
                request_id=request_id,
                market_id=market_id or "n/a",
                state=to_text(payload.get("state")).upper() or "PENDING",
                resolution_status=to_text(payload.get("resolution_status")).upper() or "PENDING",
                resolution_reason=to_text(payload.get("resolution_reason")) or reason_by_market.get(market_id, ""),
                execution_side=to_text(payload.get("side")) or "n/a",
                stake_usd=to_float(payload.get("stake_usd")),
                fill_price=to_optional_float(payload.get("fill_price")),
                order_id=to_text(payload.get("order_id")),
                retry_count=max(check_count_by_market.get(market_id, 1) - 1, 0),
                review_queue_id=review_queue_id,
                tx_intent_id=tx_intent_id,
                review_queue_url=links["review_queue_url"],
                sandbox_tx_url=links["sandbox_tx_url"],
                position_url=links["position_url"],
                updated_at=to_text(payload.get("updated_at")),
            )
        )
    pending_rows.sort(key=lambda row: row.updated_at, reverse=True)

    retry_count = sum(max(value - 1, 0) for value in check_count_by_market.values())
    available = bool(resolved_rows) or bool(pending_rows) or bool(resolution_checks)
    return SettlementTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=available,
        note="settlement_data_loaded" if available else "settlement_data_missing",
        pending_requests_count=len(pending_rows),
        resolution_checks_count=len(resolution_checks),
        settled_count=len(resolved_rows),
        resolved_settlements_count=len(resolved_rows),
        realized_pnl_usd=realized_pnl_usd,
        retry_count=retry_count,
        failure_count=failure_count,
        outcome_distribution=chart_from_counter(outcome_counter),
        pending_rows=tuple(pending_rows[:40]),
        resolved_rows=tuple(resolved_rows[:40]),
        rows=tuple(resolved_rows[:40]),
    )


def _latest_pending_requests(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for payload in rows:
        request_id = to_text(payload.get("request_id"))
        if not request_id:
            request_id = _request_id(run_id=to_text(payload.get("run_id")), payload=payload)
        current = latest.get(request_id)
        updated_at = to_text(payload.get("updated_at"))
        if current is None or updated_at >= to_text(current.get("updated_at")):
            latest[request_id] = payload
    return latest


def _review_and_tx_links(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    run_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    review_queue_by_market: dict[str, str] = {}
    for payload in queries.artifact_payloads(run_id, "trade_review_candidates"):
        market_id = extract_market_id(payload)
        queue_id = to_text(payload.get("queue_id"))
        if market_id and queue_id and market_id not in review_queue_by_market:
            review_queue_by_market[market_id] = queue_id

    tx_intent_by_market: dict[str, str] = {}
    intents = context.operational.transaction_intents.list_intents(run_id=run_id, limit=0)
    for payload in intents:
        if payload.market_id and payload.market_id not in tx_intent_by_market:
            tx_intent_by_market[payload.market_id] = intent_id(payload)
    return review_queue_by_market, tx_intent_by_market


def _request_id(*, run_id: str, payload: dict[str, object]) -> str:
    market_id = extract_market_id(payload)
    side = to_text(payload.get("side")) or "UNKNOWN"
    order_id = to_text(payload.get("order_id")) or "no_order_id"
    if run_id:
        return f"{run_id}:{market_id}:{side}:{order_id}"
    return f"{market_id}:{side}:{order_id}"


def _links(*, run_id: str, market_id: str, review_queue_id: str, tx_intent_id: str) -> dict[str, str]:
    run_token = quote_plus(run_id.strip())
    root = f"/?run_id={run_token}"
    review_queue_url = f"{root}&active_tab=review-queue&review_status=ALL"
    if review_queue_id:
        review_queue_url = f"{review_queue_url}&review_queue_id={quote_plus(review_queue_id)}"
    sandbox_tx_url = f"{root}&active_tab=sandbox-tx"
    if tx_intent_id:
        sandbox_tx_url = f"{sandbox_tx_url}&tx_intent_id={quote_plus(tx_intent_id)}"
    return {
        "review_queue_url": review_queue_url,
        "sandbox_tx_url": sandbox_tx_url,
        "position_url": f"{root}&active_tab=positions&position_market_id={quote_plus(market_id)}",
    }
