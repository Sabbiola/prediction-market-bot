from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping, Sequence

from prediction_market_bot.domain.enums import ExecutionStatus, ResolutionStatus, SettlementRequestState
from prediction_market_bot.domain.models import ExecutionResult, PendingSettlementRequest, ResolutionCheckResult
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.interfaces import PendingSettlementsRepositoryPort


class SettlementRequestQueueService:
    ARTIFACT = "pending_settlement_requests"

    def __init__(
        self,
        persistence: JsonlPersistence,
        *,
        pending_repo: PendingSettlementsRepositoryPort | None = None,
    ) -> None:
        self.persistence = persistence
        self.pending_repo = pending_repo

    def enqueue_from_execution(self, run_id: str, execution: ExecutionResult) -> PendingSettlementRequest:
        if execution.status != ExecutionStatus.FILLED:
            raise ValueError("Only FILLED executions can create pending settlement requests.")
        request_id = self._build_request_id(run_id=run_id, execution=execution)
        existing = self.get_latest(request_id)
        if existing is not None and existing.state == SettlementRequestState.PENDING:
            return existing

        now = datetime.now(UTC)
        request = PendingSettlementRequest(
            request_id=request_id,
            run_id=run_id,
            market_id=execution.market_id,
            execution_mode=execution.execution_mode,
            execution_status=execution.status,
            side=execution.side,
            stake_usd=execution.stake_usd,
            fill_price=execution.fill_price,
            order_id=execution.order_id,
            state=SettlementRequestState.PENDING,
            resolution_status=ResolutionStatus.PENDING,
            resolution_reason="awaiting_resolution",
            resolved_yes=None,
            created_at=now,
            updated_at=now,
        )
        self.persistence.write_artifact(run_id, self.ARTIFACT, request.model_dump(mode="json"))
        if self.pending_repo is not None:
            self.pending_repo.upsert_request(request)
        self.persistence.write_run_event(
            run_id,
            "pending_settlement_request_queued",
            {
                "request_id": request.request_id,
                "market_id": request.market_id,
                "order_id": request.order_id or "",
                "stake_usd": request.stake_usd,
            },
        )
        return request

    def mark_settled(
        self,
        *,
        request: PendingSettlementRequest,
        resolution: ResolutionCheckResult,
    ) -> PendingSettlementRequest:
        now = datetime.now(UTC)
        settled = request.model_copy(
            update={
                "state": SettlementRequestState.SETTLED,
                "resolution_status": resolution.status,
                "resolution_reason": resolution.reason,
                "resolved_yes": resolution.resolved_yes,
                "updated_at": now,
            }
        )
        self.persistence.write_artifact(settled.run_id, self.ARTIFACT, settled.model_dump(mode="json"))
        if self.pending_repo is not None:
            self.pending_repo.upsert_request(settled)
        self.persistence.write_run_event(
            settled.run_id,
            "pending_settlement_request_settled",
            {
                "request_id": settled.request_id,
                "market_id": settled.market_id,
                "resolved_yes": bool(settled.resolved_yes),
            },
        )
        return settled

    def list_requests(
        self,
        *,
        run_id: str | None = None,
        state: SettlementRequestState | None = None,
        limit: int = 0,
    ) -> list[PendingSettlementRequest]:
        if self.pending_repo is not None:
            state_value = state.value if state is not None else None
            return self.pending_repo.list_requests(run_id=run_id, state=state_value, limit=limit)
        latest = self._latest_requests()
        items = [
            item
            for item in sorted(latest.values(), key=lambda row: row.updated_at, reverse=True)
            if (run_id is None or item.run_id == run_id) and (state is None or item.state == state)
        ]
        if limit > 0:
            return items[:limit]
        return items

    def get_latest(self, request_id: str) -> PendingSettlementRequest | None:
        target = request_id.strip()
        if not target:
            return None
        if self.pending_repo is not None:
            request = self.pending_repo.get_request(target)
            if request is not None:
                return request
        return self._latest_requests().get(target)

    def _latest_requests(self) -> dict[str, PendingSettlementRequest]:
        rows = self.persistence.read_all_artifact_records(self.ARTIFACT)
        latest: dict[str, PendingSettlementRequest] = {}
        for row in rows:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            try:
                parsed = PendingSettlementRequest.model_validate(payload, strict=False)
            except Exception:
                continue
            current = latest.get(parsed.request_id)
            if current is None or parsed.updated_at > current.updated_at:
                latest[parsed.request_id] = parsed
        return latest

    @staticmethod
    def _build_request_id(*, run_id: str, execution: ExecutionResult) -> str:
        order_token = (execution.order_id or "").strip() or "no_order_id"
        return f"{run_id}:{execution.market_id}:{execution.side.value}:{order_token}"
