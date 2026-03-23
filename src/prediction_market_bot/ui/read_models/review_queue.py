from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote_plus

from prediction_market_bot.domain.enums import TradeReviewStatus
from prediction_market_bot.domain.models import TradeReviewItem
from prediction_market_bot.services import TradeReviewQueueService
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    ReviewQueueDetailResponse,
    ReviewQueueRowResponse,
    ReviewQueueTabResponse,
    RunSelectorResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    as_map,
    as_sequence,
    deep_get,
    extract_market_id,
    intent_id,
    is_pending_review_status,
    normalize_review_filter,
    review_status_counts,
    to_float,
    to_text,
    to_text_tuple,
)


def build_review_queue_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
    status: str | None,
    queue_id: str | None,
    limit: int,
) -> ReviewQueueTabResponse:
    queue = TradeReviewQueueService(
        context.persistence,
        candidate_repo=context.operational.review_queue,
        decision_repo=context.operational.review_decisions,
    )
    requested_filter = to_text(status).upper()
    if requested_filter == "ALL":
        active_filter: TradeReviewStatus | None = None
        active_filter_label = "ALL"
    else:
        active_filter = normalize_review_filter(status) or TradeReviewStatus.PENDING_REVIEW
        active_filter_label = active_filter.value

    all_items = queue.list_queue(run_id=selector.selected_run_id or None, status=None, limit=0)
    filtered = queue.list_queue(
        run_id=selector.selected_run_id or None,
        status=active_filter,
        limit=limit,
    )
    counts = review_status_counts(all_items)
    context_cache: dict[str, _ReviewRunContext] = {}

    def _run_context(run_id: str) -> _ReviewRunContext:
        safe_run_id = run_id.strip()
        if safe_run_id not in context_cache:
            context_cache[safe_run_id] = _build_run_context(
                run_id=safe_run_id,
                context=context,
                queries=queries,
            )
        return context_cache[safe_run_id]

    lifecycle_counter = {
        TradeReviewStatus.PENDING_REVIEW.value: 0,
        TradeReviewStatus.APPROVED.value: 0,
        TradeReviewStatus.REJECTED.value: 0,
        TradeReviewStatus.EXPIRED.value: 0,
        "EXECUTED": 0,
    }
    executed_count = 0
    for item in all_items:
        run_ctx = _run_context(item.run_id)
        effective_status = _effective_status(item)
        execution_status = _execution_status_for(item, run_ctx)
        lifecycle_state = _lifecycle_state(effective_status, execution_status)
        lifecycle_counter[lifecycle_state] = lifecycle_counter.get(lifecycle_state, 0) + 1
        if lifecycle_state == "EXECUTED":
            executed_count += 1

    rows = [_to_row(item, run_context=_run_context(item.run_id)) for item in filtered]
    if active_filter_label == TradeReviewStatus.PENDING_REVIEW.value:
        rows.sort(key=lambda item: (abs(item.edge_bps), item.confidence, item.stake_usd), reverse=True)
    else:
        rows.sort(key=lambda item: item.updated_at, reverse=True)
    rows_tuple = tuple(rows)

    selected_queue_id = queue_id.strip() if isinstance(queue_id, str) else ""
    if not selected_queue_id and rows_tuple:
        selected_queue_id = rows_tuple[0].queue_id
    selected_item_raw = queue.get_item(selected_queue_id) if selected_queue_id else None
    selected_item = (
        _to_detail(selected_item_raw, run_context=_run_context(selected_item_raw.run_id))
        if selected_item_raw is not None
        else None
    )
    logs = queries.ui_action_logs(action_prefix="review-", run_id=selector.selected_run_id, limit=12)

    return ReviewQueueTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=bool(all_items),
        note="review_queue_loaded" if all_items else "review_queue_empty",
        active_status_filter=active_filter_label,
        status_filters=(
            "ALL",
            TradeReviewStatus.PENDING_REVIEW.value,
            TradeReviewStatus.APPROVED.value,
            TradeReviewStatus.REJECTED.value,
            TradeReviewStatus.EXPIRED.value,
        ),
        pending_count=counts.get(TradeReviewStatus.PENDING_REVIEW.value, 0),
        approved_count=max(counts.get(TradeReviewStatus.APPROVED.value, 0) - executed_count, 0),
        executed_count=executed_count,
        rejected_count=counts.get(TradeReviewStatus.REJECTED.value, 0),
        expired_count=counts.get(TradeReviewStatus.EXPIRED.value, 0),
        total_count=len(all_items),
        lifecycle_distribution=(
            ChartPointResponse(
                label=TradeReviewStatus.PENDING_REVIEW.value,
                value=float(lifecycle_counter.get(TradeReviewStatus.PENDING_REVIEW.value, 0)),
            ),
            ChartPointResponse(
                label=TradeReviewStatus.APPROVED.value,
                value=float(lifecycle_counter.get(TradeReviewStatus.APPROVED.value, 0)),
            ),
            ChartPointResponse(label="EXECUTED", value=float(lifecycle_counter.get("EXECUTED", 0))),
            ChartPointResponse(
                label=TradeReviewStatus.REJECTED.value,
                value=float(lifecycle_counter.get(TradeReviewStatus.REJECTED.value, 0)),
            ),
            ChartPointResponse(
                label=TradeReviewStatus.EXPIRED.value,
                value=float(lifecycle_counter.get(TradeReviewStatus.EXPIRED.value, 0)),
            ),
        ),
        rows=rows_tuple,
        selected_queue_id=selected_queue_id,
        selected_item=selected_item,
        recent_action_logs=logs,
    )


@dataclass(slots=True)
class _ReviewRunContext:
    titles_by_market: dict[str, str]
    predictions_by_market: dict[str, dict[str, object]]
    research_by_market: dict[str, dict[str, object]]
    execution_status_by_queue: dict[str, str]
    execution_status_by_market: dict[str, str]
    intent_id_by_queue: dict[str, str]


def _build_run_context(
    *,
    run_id: str,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
) -> _ReviewRunContext:
    if not run_id:
        return _ReviewRunContext(
            titles_by_market={},
            predictions_by_market={},
            research_by_market={},
            execution_status_by_queue={},
            execution_status_by_market={},
            intent_id_by_queue={},
        )

    titles_by_market: dict[str, str] = {}
    for payload in queries.artifact_payloads(run_id, "market_snapshots"):
        market_id = extract_market_id(payload)
        if not market_id:
            continue
        title = to_text(deep_get(payload, "market", "title")) or to_text(payload.get("title"))
        if title and market_id not in titles_by_market:
            titles_by_market[market_id] = title
    for payload in queries.artifact_payloads(run_id, "market_candidates"):
        snapshot = as_map(payload.get("market"))
        market_id = extract_market_id(snapshot) or extract_market_id(payload)
        if not market_id:
            continue
        title = to_text(deep_get(snapshot, "market", "title")) or to_text(deep_get(payload, "market", "title"))
        if title and market_id not in titles_by_market:
            titles_by_market[market_id] = title

    predictions_by_market: dict[str, dict[str, object]] = {}
    for payload in queries.artifact_payloads(run_id, "prediction_results"):
        market_id = extract_market_id(payload)
        if not market_id:
            continue
        predictions_by_market[market_id] = {
            "fair_yes_prob": _optional_float(payload, "fair_yes_prob"),
            "market_yes_prob": _optional_float(payload, "market_yes_prob"),
            "rationale": to_text_tuple(payload.get("rationale")),
        }

    research_by_market: dict[str, dict[str, object]] = {}
    for payload in queries.artifact_payloads(run_id, "research_packets"):
        market_id = extract_market_id(payload)
        if not market_id:
            continue
        findings = as_sequence(payload.get("findings"))
        source_types = sorted({to_text(row.get("source_type")) for row in findings if to_text(row.get("source_type"))})
        feature_bundle = as_map(payload.get("feature_bundle"))
        research_by_market[market_id] = {
            "findings_count": len(findings),
            "source_types": tuple(source_types),
            "evidence_strength": _optional_float(payload, "evidence_strength"),
            "disagreement_score": _optional_float(payload, "disagreement_score"),
            "source_coverage_ratio": _optional_feature(payload=payload, feature_bundle=feature_bundle, key="source_coverage_ratio"),
            "freshness_hours": _optional_feature(payload=payload, feature_bundle=feature_bundle, key="freshness_hours"),
            "contradiction_score": _optional_feature(payload=payload, feature_bundle=feature_bundle, key="contradiction_score"),
            "source_diversity": _optional_feature(payload=payload, feature_bundle=feature_bundle, key="source_diversity"),
        }

    execution_status_by_queue: dict[str, str] = {}
    execution_status_by_market: dict[str, str] = {}
    for payload in queries.artifact_payloads(run_id, "execution_results"):
        status = to_text(payload.get("status")).upper()
        if not status:
            continue
        queue_id = to_text(payload.get("review_queue_id"))
        market_id = extract_market_id(payload)
        if queue_id:
            _apply_preferred_execution_status(execution_status_by_queue, queue_id, status)
        if market_id:
            _apply_preferred_execution_status(execution_status_by_market, market_id, status)

    intent_id_by_queue: dict[str, str] = {}
    for tx_intent in context.operational.transaction_intents.list_intents(run_id=run_id, limit=0):
        if tx_intent.review_queue_id and tx_intent.review_queue_id not in intent_id_by_queue:
            intent_id_by_queue[tx_intent.review_queue_id] = intent_id(tx_intent)

    return _ReviewRunContext(
        titles_by_market=titles_by_market,
        predictions_by_market=predictions_by_market,
        research_by_market=research_by_market,
        execution_status_by_queue=execution_status_by_queue,
        execution_status_by_market=execution_status_by_market,
        intent_id_by_queue=intent_id_by_queue,
    )


def _to_row(item: TradeReviewItem, *, run_context: _ReviewRunContext) -> ReviewQueueRowResponse:
    effective_status = _effective_status(item)
    execution_status = _execution_status_for(item, run_context)
    lifecycle_state = _lifecycle_state(effective_status, execution_status)
    prediction_row = run_context.predictions_by_market.get(item.market_id, {})
    research_row = run_context.research_by_market.get(item.market_id, {})
    tx_intent_id = run_context.intent_id_by_queue.get(item.queue_id, "")
    prediction_url, risk_url, sandbox_tx_url, position_url = _links(
        run_id=item.run_id,
        tx_intent_id=tx_intent_id,
        market_id=item.market_id,
    )
    return ReviewQueueRowResponse(
        queue_id=item.queue_id,
        run_id=item.run_id,
        market_id=item.market_id,
        market_title=run_context.titles_by_market.get(item.market_id, ""),
        side=item.side.value,
        status=effective_status,
        lifecycle_state=lifecycle_state,
        executed=lifecycle_state == "EXECUTED",
        execution_status=execution_status,
        fair_yes_prob=_read_optional_float(prediction_row, "fair_yes_prob"),
        market_yes_prob=_read_optional_float(prediction_row, "market_yes_prob"),
        stake_usd=item.stake_usd,
        confidence=item.confidence,
        edge_bps=round(item.edge * 10_000.0, 2),
        prediction_rationale_summary=_summary_line(item.prediction_rationale, empty="n/a"),
        risk_rationale_summary=_summary_line(item.risk_rationale, empty="n/a"),
        evidence_coverage_summary=_coverage_summary(research_row),
        findings_count=_read_int(research_row, "findings_count"),
        source_coverage_ratio=_read_optional_float(research_row, "source_coverage_ratio"),
        prediction_url=prediction_url,
        risk_url=risk_url,
        sandbox_tx_url=sandbox_tx_url,
        position_url=position_url,
        expires_at=item.expires_at.isoformat() if item.expires_at else "",
        updated_at=item.updated_at.isoformat(),
    )


def _to_detail(item: TradeReviewItem, *, run_context: _ReviewRunContext) -> ReviewQueueDetailResponse:
    effective_status = _effective_status(item)
    execution_status = _execution_status_for(item, run_context)
    lifecycle_state = _lifecycle_state(effective_status, execution_status)
    prediction_row = run_context.predictions_by_market.get(item.market_id, {})
    research_row = run_context.research_by_market.get(item.market_id, {})
    tx_intent_id = run_context.intent_id_by_queue.get(item.queue_id, "")
    prediction_url, risk_url, sandbox_tx_url, position_url = _links(
        run_id=item.run_id,
        tx_intent_id=tx_intent_id,
        market_id=item.market_id,
    )
    can_operate = is_pending_review_status(item.status)
    return ReviewQueueDetailResponse(
        queue_id=item.queue_id,
        run_id=item.run_id,
        market_id=item.market_id,
        market_title=run_context.titles_by_market.get(item.market_id, ""),
        side=item.side.value,
        status=effective_status,
        lifecycle_state=lifecycle_state,
        executed=lifecycle_state == "EXECUTED",
        execution_status=execution_status,
        stake_usd=item.stake_usd,
        confidence=item.confidence,
        edge_bps=round(item.edge * 10_000.0, 2),
        fair_yes_prob=_read_optional_float(prediction_row, "fair_yes_prob"),
        market_yes_prob=_read_optional_float(prediction_row, "market_yes_prob"),
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        expires_at=item.expires_at.isoformat() if item.expires_at else "",
        findings_count=_read_int(research_row, "findings_count"),
        source_types=_read_str_tuple(research_row, "source_types"),
        evidence_strength=_read_optional_float(research_row, "evidence_strength"),
        disagreement_score=_read_optional_float(research_row, "disagreement_score"),
        source_coverage_ratio=_read_optional_float(research_row, "source_coverage_ratio"),
        freshness_hours=_read_optional_float(research_row, "freshness_hours"),
        contradiction_score=_read_optional_float(research_row, "contradiction_score"),
        source_diversity=_read_optional_float(research_row, "source_diversity"),
        evidence_coverage_summary=_coverage_summary(research_row),
        prediction_rationale=item.prediction_rationale,
        risk_rationale=item.risk_rationale,
        model_rationale=item.model_rationale,
        operator_rationale=item.operator_rationale,
        notes=item.notes,
        can_approve=can_operate,
        can_reject=can_operate,
        prediction_url=prediction_url,
        risk_url=risk_url,
        sandbox_tx_url=sandbox_tx_url,
        position_url=position_url,
    )


def _effective_status(item: TradeReviewItem) -> str:
    if is_pending_review_status(item.status):
        return TradeReviewStatus.PENDING_REVIEW.value
    return item.status.value


def _execution_status_for(item: TradeReviewItem, run_context: _ReviewRunContext) -> str:
    if item.queue_id in run_context.execution_status_by_queue:
        return run_context.execution_status_by_queue[item.queue_id]
    return run_context.execution_status_by_market.get(item.market_id, "")


def _lifecycle_state(status: str, execution_status: str) -> str:
    if status == TradeReviewStatus.APPROVED.value and execution_status in {"SUBMITTED", "FILLED", "SETTLED"}:
        return "EXECUTED"
    return status


def _execution_priority(status: str) -> int:
    priorities = {
        "SETTLED": 5,
        "FILLED": 4,
        "SUBMITTED": 3,
        "FAILED": 2,
        "SKIPPED": 1,
    }
    return priorities.get(status, 0)


def _apply_preferred_execution_status(target: dict[str, str], key: str, status: str) -> None:
    current = target.get(key, "")
    if _execution_priority(status) >= _execution_priority(current):
        target[key] = status


def _links(*, run_id: str, tx_intent_id: str, market_id: str) -> tuple[str, str, str, str]:
    run_token = quote_plus(run_id.strip())
    market_token = quote_plus(market_id.strip())
    root = f"/?run_id={run_token}"
    sandbox_tx_url = f"{root}&active_tab=sandbox-tx"
    if tx_intent_id:
        sandbox_tx_url = f"{sandbox_tx_url}&tx_intent_id={quote_plus(tx_intent_id)}"
    return (
        f"{root}&active_tab=prediction",
        f"{root}&active_tab=risk",
        sandbox_tx_url,
        f"{root}&active_tab=positions&position_market_id={market_token}",
    )


def _optional_float(payload: dict[str, object], key: str) -> float | None:
    if key not in payload:
        return None
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return to_float(value)


def _optional_feature(
    *,
    payload: dict[str, object],
    feature_bundle: dict[str, object],
    key: str,
) -> float | None:
    value = _optional_float(feature_bundle, key)
    if value is not None:
        return value
    return _optional_float(payload, key)


def _read_optional_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _read_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _read_str_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, tuple) else ()


def _summary_line(parts: tuple[str, ...], *, empty: str) -> str:
    if not parts:
        return empty
    snippet = [part for part in parts[:2] if part.strip()]
    if not snippet:
        return empty
    text = " | ".join(snippet)
    if len(parts) > 2:
        text = f"{text} | +{len(parts) - 2} more"
    return text


def _coverage_summary(payload: dict[str, object]) -> str:
    findings_count = _read_int(payload, "findings_count")
    evidence = _read_optional_float(payload, "evidence_strength")
    source_coverage = _read_optional_float(payload, "source_coverage_ratio")
    contradiction = _read_optional_float(payload, "contradiction_score")
    parts = [f"findings={findings_count}"]
    if evidence is not None:
        parts.append(f"evidence={evidence:.2f}")
    if source_coverage is not None:
        parts.append(f"coverage={source_coverage:.2f}")
    if contradiction is not None:
        parts.append(f"contradiction={contradiction:.2f}")
    return " | ".join(parts)
