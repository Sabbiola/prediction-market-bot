from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

from prediction_market_bot.domain.models import TxIntent
from prediction_market_bot.ui.models import ExecutionRowResponse, ExecutionTabResponse, RunSelectorResponse

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    chart_from_counter,
    empty_execution,
    intent_id,
    to_float,
    to_optional_float,
    to_text,
)


def build_execution_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> ExecutionTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_execution(selector, "no_run_selected")

    executions = queries.artifact_payloads(run, "execution_results")
    intents_payload = queries.artifact_payloads(run, "order_intents")
    review_by_queue = _review_decisions(queries=queries, run_id=run)
    tx_links = _tx_links(context=context, queries=queries, run_id=run)

    intent_by_market: dict[str, dict[str, object]] = {}
    for payload in intents_payload:
        market_id = to_text(payload.get("market_id"))
        if market_id:
            intent_by_market[market_id] = payload

    status_counter: dict[str, int] = {}
    lifecycle_counter = {"SKIPPED": 0, "SUBMITTED": 0, "COMPLETED": 0, "FAILED": 0}
    rows: list[ExecutionRowResponse] = []

    for payload in executions:
        market_id = to_text(payload.get("market_id"))
        status = to_text(payload.get("status")).upper() or "UNKNOWN"
        status_counter[status] = status_counter.get(status, 0) + 1
        lifecycle_path = _lifecycle_path(status)
        if lifecycle_path in lifecycle_counter:
            lifecycle_counter[lifecycle_path] += 1
        intent_payload = intent_by_market.get(market_id, {})
        queue_id = to_text(payload.get("review_queue_id")) or to_text(intent_payload.get("review_queue_id"))
        review_status, review_rationale = review_by_queue.get(queue_id, ("", ""))
        tx_intent_id = tx_links.intent_id_by_market.get(market_id, "")
        if not tx_intent_id:
            tx_intent_id = tx_links.intent_id_by_queue.get(queue_id, "")
        links = _panel_links(
            run_id=run,
            market_id=market_id,
            queue_id=queue_id,
            tx_intent_id=tx_intent_id,
        )
        rows.append(
            ExecutionRowResponse(
                market_id=market_id or "n/a",
                status=status,
                lifecycle_path=lifecycle_path,
                side=to_text(payload.get("side")) or "n/a",
                stake_usd=to_float(payload.get("stake_usd")),
                fill_price=to_optional_float(payload.get("fill_price")),
                order_id=to_text(payload.get("order_id")),
                execution_mode=to_text(payload.get("execution_mode")),
                intent_rationale=to_text(intent_payload.get("rationale")),
                review_queue_id=queue_id,
                review_status=review_status,
                review_rationale=review_rationale,
                review_queue_url=links["review_queue_url"],
                sandbox_tx_url=links["sandbox_tx_url"],
                position_url=links["position_url"],
                message=to_text(payload.get("message")),
            )
        )
    rows.sort(key=lambda item: (item.lifecycle_path, item.stake_usd), reverse=True)
    linked_review_decisions_count = sum(1 for row in rows if row.review_queue_id and row.review_status)
    return ExecutionTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=len(rows) > 0,
        note="execution_data_loaded" if rows else "execution_data_missing",
        intents_count=len(intent_by_market),
        executions_count=len(rows),
        linked_review_decisions_count=linked_review_decisions_count,
        skipped_count=lifecycle_counter["SKIPPED"],
        submitted_count=lifecycle_counter["SUBMITTED"],
        completed_count=lifecycle_counter["COMPLETED"],
        failed_count=lifecycle_counter["FAILED"],
        status_distribution=chart_from_counter(status_counter),
        lifecycle_distribution=(
            chart_from_counter(lifecycle_counter, limit=4)
            if rows
            else ()
        ),
        rows=tuple(rows[:40]),
    )


def _lifecycle_path(status: str) -> str:
    normalized = status.strip().upper()
    if normalized == "SKIPPED":
        return "SKIPPED"
    if normalized == "SUBMITTED":
        return "SUBMITTED"
    if normalized in {"FILLED", "SETTLED"}:
        return "COMPLETED"
    if normalized in {"FAILED", "DROPPED", "REJECTED"}:
        return "FAILED"
    return "SUBMITTED" if normalized else "UNKNOWN"


def _review_decisions(*, queries: UiReadQueryService, run_id: str) -> dict[str, tuple[str, str]]:
    latest_by_queue: dict[str, dict[str, object]] = {}
    for payload in queries.artifact_payloads(run_id, "trade_review_decisions"):
        queue_id = to_text(payload.get("queue_id"))
        if not queue_id:
            continue
        current = latest_by_queue.get(queue_id)
        decided_at = to_text(payload.get("decided_at"))
        if current is None or decided_at >= to_text(current.get("decided_at")):
            latest_by_queue[queue_id] = payload

    result: dict[str, tuple[str, str]] = {}
    for queue_id, payload in latest_by_queue.items():
        result[queue_id] = (
            to_text(payload.get("status_after_action")).upper(),
            to_text(payload.get("operator_rationale")),
        )
    return result


class _TxLinks:
    def __init__(self) -> None:
        self.intent_id_by_market: dict[str, str] = {}
        self.intent_id_by_queue: dict[str, str] = {}


def _tx_links(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    run_id: str,
) -> _TxLinks:
    links = _TxLinks()
    for row in context.operational.transaction_intents.list_intents(run_id=run_id, limit=0):
        row_intent_id = intent_id(row)
        if row.market_id and row.market_id not in links.intent_id_by_market:
            links.intent_id_by_market[row.market_id] = row_intent_id
        if row.review_queue_id and row.review_queue_id not in links.intent_id_by_queue:
            links.intent_id_by_queue[row.review_queue_id] = row_intent_id
    if links.intent_id_by_market:
        return links

    for payload in queries.artifact_payloads(run_id, "order_intents"):
        try:
            parsed = TxIntent.model_validate(payload, strict=False)
        except Exception:
            continue
        row_intent_id = intent_id(parsed)
        if parsed.market_id and parsed.market_id not in links.intent_id_by_market:
            links.intent_id_by_market[parsed.market_id] = row_intent_id
        if parsed.review_queue_id and parsed.review_queue_id not in links.intent_id_by_queue:
            links.intent_id_by_queue[parsed.review_queue_id] = row_intent_id
    return links


def _panel_links(*, run_id: str, market_id: str, queue_id: str, tx_intent_id: str) -> dict[str, str]:
    run_token = quote_plus(run_id.strip())
    root = f"/?run_id={run_token}"
    review_url = f"{root}&active_tab=review-queue&review_status=ALL"
    if queue_id:
        review_url = f"{review_url}&review_queue_id={quote_plus(queue_id)}"
    sandbox_url = f"{root}&active_tab=sandbox-tx"
    if tx_intent_id:
        sandbox_url = f"{sandbox_url}&tx_intent_id={quote_plus(tx_intent_id)}"
    return {
        "review_queue_url": review_url,
        "sandbox_tx_url": sandbox_url,
        "position_url": f"{root}&active_tab=positions&position_market_id={quote_plus(market_id)}",
    }
