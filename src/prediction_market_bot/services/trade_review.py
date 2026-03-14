from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence, TypeVar

from prediction_market_bot.domain.enums import TradeReviewAction, TradeReviewStatus
from prediction_market_bot.domain.models import (
    MarketCandidate,
    PredictionResult,
    RiskDecision,
    TradeReviewCandidate,
    TradeReviewDecision,
    TradeReviewItem,
)
from prediction_market_bot.infrastructure.persistence import JsonlPersistence
from prediction_market_bot.interfaces import ReviewDecisionRepositoryPort, ReviewQueueRepositoryPort

TReviewModel = TypeVar("TReviewModel", TradeReviewCandidate, TradeReviewDecision)


class TradeReviewQueueService:
    CANDIDATE_ARTIFACT = "trade_review_candidates"
    DECISION_ARTIFACT = "trade_review_decisions"

    def __init__(
        self,
        persistence: JsonlPersistence,
        *,
        candidate_repo: ReviewQueueRepositoryPort | None = None,
        decision_repo: ReviewDecisionRepositoryPort | None = None,
    ) -> None:
        self.persistence = persistence
        self.candidate_repo = candidate_repo
        self.decision_repo = decision_repo

    def enqueue_candidate(
        self,
        run_id: str,
        candidate: MarketCandidate,
        prediction: PredictionResult,
        risk: RiskDecision,
        *,
        expires_in_seconds: int = 0,
    ) -> TradeReviewCandidate:
        if not risk.approved:
            raise ValueError("Only approved risk decisions can be queued for operator review.")
        queue_id = self._build_queue_id(run_id=run_id, market_id=candidate.market.market_id, side=prediction.selected_side.value)
        existing = self.get_candidate(queue_id)
        if existing is not None:
            return existing

        model_rationale = self._dedupe_texts((*prediction.rationale, *risk.reasoning))
        created_at = datetime.now(UTC)
        expires_at = None
        if expires_in_seconds > 0:
            expires_at = created_at + timedelta(seconds=expires_in_seconds)

        queued = TradeReviewCandidate(
            queue_id=queue_id,
            run_id=run_id,
            market_id=candidate.market.market_id,
            side=prediction.selected_side,
            stake_usd=risk.stake_usd,
            confidence=prediction.confidence,
            edge=prediction.edge,
            prediction_rationale=prediction.rationale,
            risk_rationale=risk.reasoning,
            model_rationale=model_rationale,
            expires_at=expires_at,
            created_at=created_at,
        )
        self.persistence.write_artifact(run_id, self.CANDIDATE_ARTIFACT, queued.model_dump(mode="json"))
        if self.candidate_repo is not None:
            self.candidate_repo.upsert_candidate(queued)
        self.persistence.write_run_event(
            run_id,
            "trade_review_candidate_queued",
            {
                "queue_id": queued.queue_id,
                "market_id": queued.market_id,
                "side": queued.side.value,
                "stake_usd": queued.stake_usd,
                "expires_at": queued.expires_at.isoformat() if queued.expires_at else "",
            },
        )
        return queued

    def list_queue(
        self,
        *,
        run_id: str | None = None,
        status: TradeReviewStatus | None = None,
        limit: int = 50,
    ) -> list[TradeReviewItem]:
        candidates = self._load_candidates()
        decisions_by_queue = self._group_decisions()
        items: list[TradeReviewItem] = []

        for candidate in sorted(candidates, key=lambda item: item.created_at, reverse=True):
            if run_id is not None and candidate.run_id != run_id:
                continue
            item = self._build_item(candidate, decisions_by_queue.get(candidate.queue_id, ()))
            if status is not None and not self._matches_status(item.status, status):
                continue
            items.append(item)

        if limit > 0:
            return items[:limit]
        return items

    def get_candidate(self, queue_id: str) -> TradeReviewCandidate | None:
        target = queue_id.strip()
        if not target:
            return None
        if self.candidate_repo is not None:
            candidate = self.candidate_repo.get_candidate(target)
            if candidate is not None:
                return candidate
        for candidate in self._load_candidates():
            if candidate.queue_id == target:
                return candidate
        return None

    def get_item(self, queue_id: str) -> TradeReviewItem | None:
        candidate = self.get_candidate(queue_id)
        if candidate is None:
            return None
        decisions_by_queue = self._group_decisions()
        return self._build_item(candidate, decisions_by_queue.get(candidate.queue_id, ()))

    def apply_action(
        self,
        *,
        queue_id: str,
        action: TradeReviewAction,
        operator_id: str,
        operator_rationale: str,
        note: str = "",
    ) -> TradeReviewItem:
        candidate = self.get_candidate(queue_id)
        if candidate is None:
            raise KeyError(f"review queue item not found: {queue_id}")

        current = self.get_item(queue_id)
        current_status = current.status if current is not None else TradeReviewStatus.PENDING_REVIEW
        status_after = self._status_after(action=action, current_status=current_status)
        decision = TradeReviewDecision(
            queue_id=candidate.queue_id,
            run_id=candidate.run_id,
            market_id=candidate.market_id,
            action=action,
            status_after_action=status_after,
            operator_id=operator_id.strip(),
            operator_rationale=operator_rationale.strip(),
            note=note.strip(),
        )
        self.persistence.write_artifact(candidate.run_id, self.DECISION_ARTIFACT, decision.model_dump(mode="json"))
        if self.decision_repo is not None:
            self.decision_repo.append_decision(decision)
        self.persistence.write_run_event(
            candidate.run_id,
            "trade_review_decision_recorded",
            {
                "queue_id": decision.queue_id,
                "market_id": decision.market_id,
                "action": decision.action.value,
                "status_after_action": decision.status_after_action.value,
                "operator_id": decision.operator_id,
            },
        )
        item = self.get_item(queue_id)
        if item is None:
            raise RuntimeError("failed to rebuild review item after decision write")
        return item

    @staticmethod
    def _build_queue_id(*, run_id: str, market_id: str, side: str) -> str:
        return f"{run_id}:{market_id}:{side}"

    def _group_decisions(self) -> dict[str, tuple[TradeReviewDecision, ...]]:
        grouped: dict[str, list[TradeReviewDecision]] = {}
        for decision in self._load_decisions():
            grouped.setdefault(decision.queue_id, []).append(decision)
        return {
            key: tuple(sorted(rows, key=lambda item: item.decided_at))
            for key, rows in grouped.items()
        }

    @classmethod
    def _build_item(
        cls,
        candidate: TradeReviewCandidate,
        decisions: Sequence[TradeReviewDecision],
    ) -> TradeReviewItem:
        status = TradeReviewStatus.PENDING_REVIEW
        operator_rationale = ""
        notes: list[str] = []
        updated_at = candidate.created_at

        for decision in decisions:
            updated_at = max(updated_at, decision.decided_at)
            status = decision.status_after_action
            if decision.action == TradeReviewAction.NOTE:
                note_text = decision.note.strip() or decision.operator_rationale.strip()
                if note_text:
                    notes.append(f"{decision.operator_id}: {note_text}")
                continue
            operator_rationale = decision.operator_rationale
            if decision.note.strip():
                notes.append(f"{decision.operator_id}: {decision.note.strip()}")

        expired = (
            cls._is_pending_status(status)
            and candidate.expires_at is not None
            and datetime.now(UTC) >= candidate.expires_at
        )
        if expired:
            status = TradeReviewStatus.EXPIRED
            expires_at = candidate.expires_at
            if expires_at is not None:
                updated_at = max(updated_at, expires_at)

        return TradeReviewItem(
            queue_id=candidate.queue_id,
            run_id=candidate.run_id,
            market_id=candidate.market_id,
            side=candidate.side,
            stake_usd=candidate.stake_usd,
            confidence=candidate.confidence,
            edge=candidate.edge,
            status=status,
            prediction_rationale=candidate.prediction_rationale,
            risk_rationale=candidate.risk_rationale,
            model_rationale=candidate.model_rationale,
            operator_rationale=operator_rationale,
            notes=tuple(notes),
            expires_at=candidate.expires_at,
            created_at=candidate.created_at,
            updated_at=updated_at,
        )

    def _load_candidates(self) -> list[TradeReviewCandidate]:
        if self.candidate_repo is not None:
            records = self.candidate_repo.list_candidates(limit=0)
            if records:
                return records
        return self._load_artifact_models(self.CANDIDATE_ARTIFACT, TradeReviewCandidate)

    def _load_decisions(self) -> list[TradeReviewDecision]:
        if self.decision_repo is not None:
            records = self.decision_repo.list_decisions()
            if records:
                return records
        return self._load_artifact_models(self.DECISION_ARTIFACT, TradeReviewDecision)

    def _load_artifact_models(self, artifact_type: str, model_type: type[TReviewModel]) -> list[TReviewModel]:
        rows = self.persistence.read_all_artifact_records(artifact_type)
        records: list[TReviewModel] = []
        for row in rows:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            try:
                parsed = model_type.model_validate(payload, strict=False)
            except Exception:
                continue
            records.append(parsed)
        return records

    @staticmethod
    def _status_after(action: TradeReviewAction, current_status: TradeReviewStatus) -> TradeReviewStatus:
        if action == TradeReviewAction.APPROVE:
            return TradeReviewStatus.APPROVED
        if action == TradeReviewAction.REJECT:
            return TradeReviewStatus.REJECTED
        if current_status == TradeReviewStatus.PENDING:
            return TradeReviewStatus.PENDING_REVIEW
        return current_status

    @staticmethod
    def _is_pending_status(status: TradeReviewStatus) -> bool:
        return status in {TradeReviewStatus.PENDING_REVIEW, TradeReviewStatus.PENDING}

    @classmethod
    def _matches_status(cls, current: TradeReviewStatus, target: TradeReviewStatus) -> bool:
        if current == target:
            return True
        return cls._is_pending_status(current) and cls._is_pending_status(target)

    @staticmethod
    def _dedupe_texts(values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in values:
            text = raw.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return tuple(result)
