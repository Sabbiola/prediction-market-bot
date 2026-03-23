from __future__ import annotations

import json
import logging
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.domain.enums import TradeReviewAction
from prediction_market_bot.domain.models import TradeReviewItem
from prediction_market_bot.services import TradeReviewQueueService

from prediction_market_bot.cli.common import parse_review_action, parse_review_status, require_cli_role

logger = logging.getLogger(__name__)


def _print_review_item(item: TradeReviewItem) -> None:
    expires_at = item.expires_at
    print(
        f"- queue_id={item.queue_id} run_id={item.run_id} market_id={item.market_id} "
        f"status={item.status.value} side={item.side.value} stake_usd={item.stake_usd:.2f} "
        f"confidence={item.confidence:.4f} edge={item.edge:.4f}"
    )
    if expires_at is not None:
        print(f"  expires_at={expires_at.isoformat()}")
    if item.model_rationale:
        print(f"  model_rationale={'; '.join(item.model_rationale)}")
    if item.operator_rationale:
        print(f"  operator_rationale={item.operator_rationale}")
    if item.notes:
        print(f"  notes={'; '.join(item.notes)}")


def review_list_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None,
    status: str,
    limit: int,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    status_filter = parse_review_status(status)

    items = queue.list_queue(
        run_id=run_id,
        status=status_filter,
        limit=limit,
    )
    payload = [item.model_dump(mode="json") for item in items]

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Review queue items: {len(items)}")
        for item in items:
            _print_review_item(item)

    logger.info(
        "review_queue_listed",
        extra={
            "event": "review_queue_listed",
            "run_id_filter": run_id or "",
            "status_filter": status_filter.value if status_filter is not None else "ALL",
            "item_count": len(items),
            "limit": limit,
        },
    )
    return 0


def review_show_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    as_json: bool,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )
    item = queue.get_item(queue_id.strip())
    if item is None:
        print(f"Review queue item not found: {queue_id}")
        return 1

    payload = item.model_dump(mode="json")
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        _print_review_item(item)

    logger.info(
        "review_queue_item_shown",
        extra={
            "event": "review_queue_item_shown",
            "queue_id": item.queue_id,
            "status": item.status.value,
            "run_id": item.run_id,
        },
    )
    return 0


def _review_action_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    action: TradeReviewAction,
    operator_id: str,
    rationale: str,
    note: str,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="operator",
            action_name=f"review-{action.value.lower()}",
            acting_user=acting_user or operator_id,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"Review action blocked: {exc}")
        return 2
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    queue = TradeReviewQueueService(
        persistence,
        candidate_repo=operational.review_queue,
        decision_repo=operational.review_decisions,
    )

    try:
        item = queue.apply_action(
            queue_id=queue_id,
            action=action,
            operator_id=operator_id,
            operator_rationale=rationale,
            note=note,
        )
    except KeyError:
        print(f"Review queue item not found: {queue_id}")
        return 1

    print(json.dumps(item.model_dump(mode="json"), indent=2))
    logger.info(
        "review_action_recorded",
        extra={
            "event": "review_action_recorded",
            "queue_id": queue_id,
            "action": action.value,
            "operator_id": operator_id,
            "acting_user": actor_user,
            "acting_role": actor_role,
            "status_after_action": item.status.value,
        },
    )
    return 0


def review_action_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    action: str,
    operator_id: str,
    rationale: str,
    note: str,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=parse_review_action(action),
        operator_id=operator_id,
        rationale=rationale,
        note=note,
        acting_user=acting_user,
        acting_role=acting_role,
    )


def review_approve_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    operator_id: str,
    rationale: str,
    note: str,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=TradeReviewAction.APPROVE,
        operator_id=operator_id,
        rationale=rationale,
        note=note,
        acting_user=acting_user,
        acting_role=acting_role,
    )


def review_reject_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    queue_id: str,
    operator_id: str,
    rationale: str,
    note: str,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    return _review_action_command(
        config_path,
        agents_config_path,
        queue_id=queue_id,
        action=TradeReviewAction.REJECT,
        operator_id=operator_id,
        rationale=rationale,
        note=note,
        acting_user=acting_user,
        acting_role=acting_role,
    )
