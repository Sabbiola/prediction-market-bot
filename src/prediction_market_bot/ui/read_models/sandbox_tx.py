from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote_plus

from prediction_market_bot.domain.enums import TxConfirmationStatus
from prediction_market_bot.ui.models import (
    RunSelectorResponse,
    SandboxTxAttemptTimelineRowResponse,
    SandboxTxIntentDetailResponse,
    SandboxTxReceiptTimelineRowResponse,
    SandboxTxRowResponse,
    SandboxTxTabResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    attempt_sort_key,
    chart_from_counter,
    empty_sandbox_tx,
    extract_market_id,
    intent_id,
    status_priority,
    to_text,
)


def build_sandbox_tx_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
    intent_id_filter: str | None,
) -> SandboxTxTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_sandbox_tx(selector, "no_run_selected")
    attempts = context.operational.transaction_attempts.list_attempts(run_id=run, limit=0)
    receipts = context.operational.transaction_receipts.list_receipts(run_id=run, limit=0)
    if not attempts:
        attempts = queries.attempts_from_artifacts(run)
    intents = context.operational.transaction_intents.list_intents(run_id=run, limit=0)
    review_queue_by_market = _review_queue_by_market(queries=queries, run_id=run)
    receipts_by_intent = {
        row.intent_id: row
        for row in sorted(receipts, key=lambda item: item.confirmed_at or datetime.min.replace(tzinfo=UTC))
        if row.intent_id
    }

    counter: dict[str, int] = {}
    rows: list[SandboxTxRowResponse] = []
    latest_by_intent = {}
    for attempt in attempts:
        status = attempt.confirmation_status.value
        counter[status] = counter.get(status, 0) + 1
        previous = latest_by_intent.get(attempt.intent_id)
        if previous is None or attempt_sort_key(attempt) > attempt_sort_key(previous):
            latest_by_intent[attempt.intent_id] = attempt

        review_queue_id = attempt.review_queue_id or review_queue_by_market.get(attempt.market_id, "")
        links = _panel_links(
            run_id=run,
            market_id=attempt.market_id,
            review_queue_id=review_queue_id,
        )
        has_receipt = attempt.intent_id in receipts_by_intent
        rows.append(
            SandboxTxRowResponse(
                intent_id=attempt.intent_id,
                review_queue_id=review_queue_id,
                market_id=attempt.market_id,
                side=attempt.side.value,
                execution_mode=attempt.execution_mode.value,
                confirmation_status=status,
                has_receipt=has_receipt,
                reconcile_state=_reconcile_state(
                    confirmation_status=status,
                    has_receipt=has_receipt,
                ),
                tx_hash=attempt.tx_hash or "",
                nonce=attempt.nonce,
                retry_count=attempt.retry_count,
                replacement_for_tx_hash=attempt.replacement_for_tx_hash,
                replaced_by_tx_hash=attempt.replaced_by_tx_hash,
                review_queue_url=links["review_queue_url"],
                position_url=links["position_url"],
                message=attempt.message,
            )
        )
    rows.sort(key=lambda item: status_priority(item.confirmation_status), reverse=True)
    selected_intent_id = intent_id_filter.strip() if isinstance(intent_id_filter, str) else ""
    if not selected_intent_id and rows:
        selected_intent_id = rows[0].intent_id
    selected_intent_raw = next((item for item in intents if intent_id(item) == selected_intent_id), None)
    selected_intent = (
        SandboxTxIntentDetailResponse(
            intent_id=intent_id(selected_intent_raw),
            run_id=selected_intent_raw.run_id,
            market_id=selected_intent_raw.market_id,
            review_queue_id=selected_intent_raw.review_queue_id,
            venue=selected_intent_raw.venue,
            side=selected_intent_raw.side.value,
            stake_usd=selected_intent_raw.stake_usd,
            limit_price=selected_intent_raw.limit_price,
            rationale=selected_intent_raw.rationale,
        )
        if selected_intent_raw is not None
        else None
    )
    attempt_timeline = tuple(
        SandboxTxAttemptTimelineRowResponse(
            submitted_at=item.submitted_at.isoformat() if item.submitted_at else "",
            confirmation_status=item.confirmation_status.value,
            status=item.status.value,
            tx_hash=item.tx_hash or "",
            nonce=item.nonce,
            retry_count=item.retry_count,
            replacement_for_tx_hash=item.replacement_for_tx_hash,
            replaced_by_tx_hash=item.replaced_by_tx_hash,
            message=item.message,
        )
        for item in sorted(
            [row for row in attempts if row.intent_id == selected_intent_id],
            key=attempt_sort_key,
        )
    )
    receipt_timeline = tuple(
        SandboxTxReceiptTimelineRowResponse(
            submitted_at=item.submitted_at.isoformat() if item.submitted_at else "",
            confirmed_at=item.confirmed_at.isoformat() if item.confirmed_at else "",
            status=item.status.value,
            confirmation_status=item.confirmation_status.value,
            tx_hash=item.tx_hash or "",
            nonce=item.nonce,
            order_id=item.order_id or "",
            message=item.message,
        )
        for item in sorted(
            [row for row in receipts if row.intent_id == selected_intent_id],
            key=lambda row: row.submitted_at or datetime.min.replace(tzinfo=UTC),
        )
    )
    selected_latest = latest_by_intent.get(selected_intent_id)
    can_resubmit = (
        selected_latest is not None
        and context.settings.sandbox_chain.submit_tx
        and selected_latest.confirmation_status not in {TxConfirmationStatus.PENDING, TxConfirmationStatus.MINED}
    )
    if not context.settings.sandbox_chain.submit_tx:
        action_hint = "resubmit_disabled_submit_tx_false"
    elif selected_latest is None:
        action_hint = "no_attempt_selected"
    elif can_resubmit:
        action_hint = "resubmit_safe_allowed"
    else:
        action_hint = "resubmit_blocked_pending_or_mined"
    logs = queries.ui_action_logs(action_prefix="tx-", run_id=run, limit=12)

    latest_counter = {status.value: 0 for status in TxConfirmationStatus}
    reconcile_backlog_count = 0
    for latest in latest_by_intent.values():
        key = latest.confirmation_status.value
        latest_counter[key] = latest_counter.get(key, 0) + 1
        if key in {TxConfirmationStatus.PENDING.value, TxConfirmationStatus.UNKNOWN.value}:
            reconcile_backlog_count += 1

    return SandboxTxTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=bool(rows),
        note="sandbox_tx_data_loaded" if rows else "sandbox_tx_data_missing",
        attempts_count=len(rows),
        receipts_count=len(receipts),
        pending_count=latest_counter.get(TxConfirmationStatus.PENDING.value, 0),
        mined_count=latest_counter.get(TxConfirmationStatus.MINED.value, 0),
        failed_count=latest_counter.get(TxConfirmationStatus.FAILED.value, 0),
        dropped_count=latest_counter.get(TxConfirmationStatus.DROPPED.value, 0),
        replaced_count=latest_counter.get(TxConfirmationStatus.REPLACED.value, 0),
        reconcile_backlog_count=reconcile_backlog_count,
        confirmation_distribution=chart_from_counter(counter),
        rows=tuple(rows[:60]),
        active_intent_id=selected_intent_id,
        can_reconcile=bool(rows),
        can_resubmit_safe=can_resubmit,
        action_hint=action_hint,
        selected_intent=selected_intent,
        attempt_timeline=attempt_timeline,
        receipt_timeline=receipt_timeline,
        recent_action_logs=logs,
    )


def _review_queue_by_market(*, queries: UiReadQueryService, run_id: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for payload in queries.artifact_payloads(run_id, "trade_review_candidates"):
        market_id = extract_market_id(payload)
        queue_id = to_text(payload.get("queue_id"))
        if market_id and queue_id and market_id not in links:
            links[market_id] = queue_id
    return links


def _reconcile_state(*, confirmation_status: str, has_receipt: bool) -> str:
    normalized = confirmation_status.strip().upper()
    if normalized == TxConfirmationStatus.MINED.value and has_receipt:
        return "reconciled"
    if normalized in {TxConfirmationStatus.PENDING.value, TxConfirmationStatus.UNKNOWN.value}:
        return "needs_reconcile"
    if normalized == TxConfirmationStatus.MINED.value and not has_receipt:
        return "receipt_pending"
    return "attention_required"


def _panel_links(*, run_id: str, market_id: str, review_queue_id: str) -> dict[str, str]:
    run_token = quote_plus(run_id.strip())
    root = f"/?run_id={run_token}"
    review_url = f"{root}&active_tab=review-queue&review_status=ALL"
    if review_queue_id:
        review_url = f"{review_url}&review_queue_id={quote_plus(review_queue_id)}"
    return {
        "review_queue_url": review_url,
        "position_url": f"{root}&active_tab=positions&position_market_id={quote_plus(market_id)}",
    }
