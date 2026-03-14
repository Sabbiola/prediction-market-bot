from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Callable, Mapping, Sequence

from prediction_market_bot.agents.contracts import (
    ExecutionAgent,
    PostmortemAgent,
    PredictionAgent,
    ResearchAgent,
    RiskAgent,
    ScanAgent,
    SettlementAgent,
)
from prediction_market_bot.domain.enums import ExecutionStatus, OutcomeClassification
from prediction_market_bot.domain.models import (
    ExecutionResult,
    MarketCandidate,
    MarketSnapshot,
    PendingSettlementRequest,
    PostmortemReport,
    PredictionResult,
    ResearchPacket,
    RiskDecision,
    SettlementResult,
    TradeReviewCandidate,
)
from prediction_market_bot.interfaces import MarketDataPort, PersistencePort
from prediction_market_bot.interfaces import (
    RunsRepositoryPort,
    TransactionAttemptsRepositoryPort,
    TransactionIntentsRepositoryPort,
    TransactionReceiptsRepositoryPort,
)
from prediction_market_bot.services import PaperPortfolioEngine

ResolutionStrategy = Callable[[MarketSnapshot], bool]
AlertHook = Callable[[str, Mapping[str, object]], None]
ReviewQueueHook = Callable[[str, MarketCandidate, PredictionResult, RiskDecision], TradeReviewCandidate]
ReviewGateHook = Callable[[TradeReviewCandidate], tuple[bool, str]]
SettlementRequestHook = Callable[[str, ExecutionResult], PendingSettlementRequest]

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PipelineRecord:
    market_id: str
    candidate: MarketCandidate
    research: ResearchPacket
    prediction: PredictionResult
    risk: RiskDecision
    execution: ExecutionResult
    settlement: SettlementResult | None
    postmortem: PostmortemReport | None


@dataclass(slots=True, frozen=True)
class PipelineSummary:
    run_id: str
    correlation_id: str
    started_at: datetime
    finished_at: datetime
    total_markets: int
    candidates: int
    executed_count: int
    settled_count: int
    wins: int
    losses: int
    skipped: int
    stage_timings_ms: dict[str, float]
    counters: dict[str, int]
    records: tuple[PipelineRecord, ...]


class PipelineCoordinator:
    def __init__(
        self,
        *,
        market_data: MarketDataPort,
        scanner: ScanAgent,
        research: ResearchAgent,
        prediction: PredictionAgent,
        risk: RiskAgent,
        execution: ExecutionAgent,
        settlement: SettlementAgent,
        postmortem: PostmortemAgent,
        persistence: PersistencePort | None = None,
        portfolio: PaperPortfolioEngine | None = None,
        resolve_outcome: ResolutionStrategy | None = None,
        alert_hook: AlertHook | None = None,
        review_queue_hook: ReviewQueueHook | None = None,
        review_gate_hook: ReviewGateHook | None = None,
        settlement_request_hook: SettlementRequestHook | None = None,
        run_repo: RunsRepositoryPort | None = None,
        transaction_intent_repo: TransactionIntentsRepositoryPort | None = None,
        transaction_attempt_repo: TransactionAttemptsRepositoryPort | None = None,
        transaction_receipt_repo: TransactionReceiptsRepositoryPort | None = None,
        review_blocking_gate: bool = False,
        settlement_same_run: bool = True,
    ) -> None:
        self.market_data = market_data
        self.scanner = scanner
        self.research = research
        self.prediction = prediction
        self.risk = risk
        self.execution = execution
        self.settlement = settlement
        self.postmortem = postmortem
        self.persistence = persistence
        self.portfolio = portfolio
        self.resolve_outcome = resolve_outcome or self._default_resolution
        self.alert_hook = alert_hook
        self.review_queue_hook = review_queue_hook
        self.review_gate_hook = review_gate_hook
        self.settlement_request_hook = settlement_request_hook
        self.run_repo = run_repo
        self.transaction_intent_repo = transaction_intent_repo
        self.transaction_attempt_repo = transaction_attempt_repo
        self.transaction_receipt_repo = transaction_receipt_repo
        self.review_blocking_gate = review_blocking_gate
        self.settlement_same_run = settlement_same_run

    def run_dry(self, *, run_id: str | None = None) -> PipelineSummary:
        markets = tuple(self.market_data.list_active_markets())
        return self.run_once(markets=markets, run_id=run_id)

    def run_once(self, markets: Sequence[MarketSnapshot], *, run_id: str | None = None) -> PipelineSummary:
        effective_run_id = run_id or self._build_run_id()
        started_at = datetime.now(UTC)
        run_started_perf = perf_counter()
        stage_timings_ms: dict[str, float] = {}
        counters: dict[str, int] = {
            "candidate_markets": 0,
            "rejected_trades": 0,
            "executed_paper_trades": 0,
            "stale_data_events": 0,
            "source_failures": 0,
            "review_queue_candidates": 0,
            "pending_settlement_requests": 0,
        }
        current_stage = "scan"
        records: list[PipelineRecord] = []
        candidates: list[MarketCandidate] = []
        self._emit_stage("pipeline_start", run_id=effective_run_id, total_markets=len(markets))
        self._upsert_run_state(
            effective_run_id,
            {
                "run_id": effective_run_id,
                "correlation_id": effective_run_id,
                "status": "running",
                "started_at": started_at.isoformat(),
                "total_markets": len(markets),
            },
        )
        try:
            scan_started = perf_counter()
            self._emit_stage("scan_start", run_id=effective_run_id, total_markets=len(markets))
            candidates = self.scanner.run(markets)
            scan_duration_ms = self._elapsed_ms(scan_started)
            self._add_stage_timing(stage_timings_ms, "scan", scan_duration_ms)
            counters["candidate_markets"] = len(candidates)
            self._emit_stage(
                "scan_end",
                run_id=effective_run_id,
                candidates=len(candidates),
                duration_ms=round(scan_duration_ms, 3),
            )

            for market in markets:
                self._persist_artifact(effective_run_id, "market_snapshots", market.model_dump(mode="json"))
            for candidate in candidates:
                self._persist_artifact(effective_run_id, "market_candidates", candidate.model_dump(mode="json"))

            for candidate in candidates:
                market_id = candidate.market.market_id

                current_stage = "research"
                stage_started = perf_counter()
                research_packet = self.research.run(candidate)
                research_duration_ms = self._elapsed_ms(stage_started)
                self._add_stage_timing(stage_timings_ms, "research", research_duration_ms)
                source_failures = self._as_non_negative_int(getattr(self.research, "last_source_failures", 0))
                counters["source_failures"] += source_failures
                self._emit_stage(
                    "research_end",
                    run_id=effective_run_id,
                    market_id=market_id,
                    findings=len(research_packet.findings),
                    evidence_strength=research_packet.evidence_strength,
                    disagreement_score=research_packet.disagreement_score,
                    source_failures=source_failures,
                    duration_ms=round(research_duration_ms, 3),
                )
                self._persist_artifact(effective_run_id, "research_packets", research_packet.model_dump(mode="json"))

                current_stage = "prediction"
                stage_started = perf_counter()
                prediction_result = self.prediction.run(candidate, research_packet)
                prediction_duration_ms = self._elapsed_ms(stage_started)
                self._add_stage_timing(stage_timings_ms, "prediction", prediction_duration_ms)
                self._emit_stage(
                    "prediction_end",
                    run_id=effective_run_id,
                    market_id=market_id,
                    fair_yes_prob=prediction_result.fair_yes_prob,
                    edge=prediction_result.edge,
                    confidence=prediction_result.confidence,
                    selected_side=prediction_result.selected_side.value,
                    duration_ms=round(prediction_duration_ms, 3),
                )
                self._persist_artifact(effective_run_id, "prediction_results", prediction_result.model_dump(mode="json"))

                current_stage = "risk"
                stage_started = perf_counter()
                portfolio_snapshot = self.portfolio.snapshot() if self.portfolio else None
                risk_decision = self.risk.run(
                    prediction_result,
                    candidate=candidate,
                    portfolio=portfolio_snapshot,
                )
                risk_duration_ms = self._elapsed_ms(stage_started)
                self._add_stage_timing(stage_timings_ms, "risk", risk_duration_ms)
                if not risk_decision.approved:
                    counters["rejected_trades"] += 1
                stale_hits = self._count_matching_reasons(risk_decision.reasoning, token="stale_market_data")
                counters["stale_data_events"] += stale_hits
                self._emit_stage(
                    "risk_end",
                    run_id=effective_run_id,
                    market_id=market_id,
                    approved=risk_decision.approved,
                    stake_usd=risk_decision.stake_usd,
                    stale_data_events=stale_hits,
                    duration_ms=round(risk_duration_ms, 3),
                )
                self._persist_artifact(effective_run_id, "risk_decisions", risk_decision.model_dump(mode="json"))

                review_candidate: TradeReviewCandidate | None = None
                if risk_decision.approved and self.review_queue_hook is not None:
                    current_stage = "review_queue"
                    stage_started = perf_counter()
                    review_candidate = self.review_queue_hook(
                        effective_run_id,
                        candidate,
                        prediction_result,
                        risk_decision,
                    )
                    review_duration_ms = self._elapsed_ms(stage_started)
                    self._add_stage_timing(stage_timings_ms, "review_queue", review_duration_ms)
                    counters["review_queue_candidates"] += 1
                    self._emit_stage(
                        "trade_review_queued",
                        run_id=effective_run_id,
                        market_id=market_id,
                        queue_id=review_candidate.queue_id,
                        duration_ms=round(review_duration_ms, 3),
                    )

                effective_risk_decision = risk_decision
                if risk_decision.approved and review_candidate is not None and self.review_blocking_gate:
                    current_stage = "review_gate"
                    stage_started = perf_counter()
                    gate_allowed = False
                    gate_reason = "manual_review_pending"
                    if self.review_gate_hook is not None:
                        raw_allowed, raw_reason = self.review_gate_hook(review_candidate)
                        gate_allowed = bool(raw_allowed)
                        gate_reason = raw_reason.strip() if raw_reason.strip() else gate_reason
                    gate_duration_ms = self._elapsed_ms(stage_started)
                    self._add_stage_timing(stage_timings_ms, "review_gate", gate_duration_ms)
                    self._persist_artifact(
                        effective_run_id,
                        "trade_review_gate_decisions",
                        {
                            "queue_id": review_candidate.queue_id,
                            "market_id": market_id,
                            "allowed": gate_allowed,
                            "reason": gate_reason,
                        },
                    )
                    self._emit_stage(
                        "trade_review_gate",
                        run_id=effective_run_id,
                        market_id=market_id,
                        queue_id=review_candidate.queue_id,
                        allowed=gate_allowed,
                        reason=gate_reason,
                        duration_ms=round(gate_duration_ms, 3),
                    )
                    if not gate_allowed:
                        counters["rejected_trades"] += 1
                        effective_risk_decision = risk_decision.model_copy(
                            update={
                                "approved": False,
                                "stake_usd": 0.0,
                                "bankroll_fraction": 0.0,
                                "max_loss_usd": 0.0,
                                "reasoning": tuple((*risk_decision.reasoning, f"manual_review_blocked reason={gate_reason}")),
                            }
                        )

                self._persist_artifact(
                    effective_run_id,
                    "effective_risk_decisions",
                    effective_risk_decision.model_dump(mode="json"),
                )

                order_intent = None
                if effective_risk_decision.approved:
                    order_intent = self.execution.build_order_intent(effective_risk_decision, prediction_result)
                    order_intent = order_intent.model_copy(
                        update={
                            "run_id": effective_run_id,
                            "review_queue_id": review_candidate.queue_id if review_candidate is not None else "",
                        }
                    )
                    self._persist_artifact(effective_run_id, "order_intents", order_intent.model_dump(mode="json"))
                    self._persist_transaction_intent(effective_run_id, order_intent)

                current_stage = "execution"
                stage_started = perf_counter()
                execution_result = self.execution.run(
                    effective_risk_decision,
                    prediction_result,
                    order_intent=order_intent,
                )
                execution_duration_ms = self._elapsed_ms(stage_started)
                self._add_stage_timing(stage_timings_ms, "execution", execution_duration_ms)
                if execution_result.status == ExecutionStatus.FILLED:
                    counters["executed_paper_trades"] += 1
                self._emit_stage(
                    "execution_end",
                    run_id=effective_run_id,
                    market_id=market_id,
                    status=execution_result.status.value,
                    order_id=execution_result.order_id,
                    fill_price=execution_result.fill_price,
                    duration_ms=round(execution_duration_ms, 3),
                )
                self._persist_artifact(effective_run_id, "execution_results", execution_result.model_dump(mode="json"))
                execution_attempts = getattr(self.execution, "last_attempts", ())
                if isinstance(execution_attempts, Sequence):
                    for attempt in execution_attempts:
                        if hasattr(attempt, "model_dump"):
                            self._persist_artifact(
                                effective_run_id,
                                "transaction_attempts",
                                attempt.model_dump(mode="json"),  # type: ignore[union-attr]
                            )
                            self._persist_transaction_attempt(effective_run_id, attempt)  # type: ignore[arg-type]
                self._persist_transaction_receipt(effective_run_id, execution_result)
                if (
                    self.portfolio
                    and order_intent is not None
                    and execution_result.status == ExecutionStatus.FILLED
                    and execution_result.fill_price is not None
                ):
                    fill_ratio = execution_result.stake_usd / max(order_intent.stake_usd, 1e-9)
                    slippage_bps = int(round((execution_result.fill_price - order_intent.limit_price) * 10_000))
                    self.portfolio.simulate_order_fill(
                        run_id=effective_run_id,
                        order=order_intent,
                        fill_ratio=fill_ratio,
                        slippage_bps=slippage_bps,
                    )

                settlement_result: SettlementResult | None = None
                postmortem_result: PostmortemReport | None = None
                if self.settlement_same_run:
                    current_stage = "settlement"
                    stage_started = perf_counter()
                    resolved_yes = self.resolve_outcome(candidate.market)
                    settlement_result = self.settlement.settle(execution_result, resolved_yes=resolved_yes)
                    settlement_duration_ms = self._elapsed_ms(stage_started)
                    self._add_stage_timing(stage_timings_ms, "settlement", settlement_duration_ms)
                    self._emit_stage(
                        "settlement_end",
                        run_id=effective_run_id,
                        market_id=market_id,
                        resolved_yes=resolved_yes,
                        pnl_usd=settlement_result.pnl_usd,
                        outcome=settlement_result.outcome_classification.value,
                        duration_ms=round(settlement_duration_ms, 3),
                    )
                    self._persist_artifact(effective_run_id, "settlement_results", settlement_result.model_dump(mode="json"))
                    if self.portfolio:
                        self.portfolio.settle_market(
                            run_id=effective_run_id,
                            market_id=market_id,
                            resolved_yes=resolved_yes,
                        )

                    current_stage = "postmortem"
                    stage_started = perf_counter()
                    postmortem_result = self.postmortem.run(settlement_result, prediction_result, research_packet)
                    postmortem_duration_ms = self._elapsed_ms(stage_started)
                    self._add_stage_timing(stage_timings_ms, "postmortem", postmortem_duration_ms)
                    self._emit_stage(
                        "postmortem_end",
                        run_id=effective_run_id,
                        market_id=market_id,
                        outcome=postmortem_result.outcome_classification.value,
                        causes=len(postmortem_result.causes),
                        action_items=len(postmortem_result.action_items),
                        duration_ms=round(postmortem_duration_ms, 3),
                    )
                    self._persist_artifact(effective_run_id, "postmortems", postmortem_result.model_dump(mode="json"))
                else:
                    current_stage = "settlement_deferred"
                    stage_started = perf_counter()
                    settlement_request_id = ""
                    if execution_result.status == ExecutionStatus.FILLED and self.settlement_request_hook is not None:
                        request = self.settlement_request_hook(effective_run_id, execution_result)
                        settlement_request_id = request.request_id
                        counters["pending_settlement_requests"] += 1
                    deferred_duration_ms = self._elapsed_ms(stage_started)
                    self._add_stage_timing(stage_timings_ms, "settlement_deferred", deferred_duration_ms)
                    self._emit_stage(
                        "settlement_deferred",
                        run_id=effective_run_id,
                        market_id=market_id,
                        execution_status=execution_result.status.value,
                        settlement_request_id=settlement_request_id,
                        duration_ms=round(deferred_duration_ms, 3),
                    )

                records.append(
                    PipelineRecord(
                        market_id=market_id,
                        candidate=candidate,
                        research=research_packet,
                        prediction=prediction_result,
                        risk=effective_risk_decision,
                        execution=execution_result,
                        settlement=settlement_result,
                        postmortem=postmortem_result,
                    )
                )
        except Exception as exc:
            finished_at = datetime.now(UTC)
            self._add_stage_timing(stage_timings_ms, "total", self._elapsed_ms(run_started_perf))
            rounded_timings = self._rounded_stage_timings(stage_timings_ms)
            settled_records = [item for item in records if item.settlement is not None]
            failed_summary_payload = {
                "run_id": effective_run_id,
                "correlation_id": effective_run_id,
                "status": "failed",
                "failed_stage": current_stage,
                "error": str(exc),
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "total_markets": len(markets),
                "candidates": counters["candidate_markets"],
                "executed_count": counters["executed_paper_trades"],
                "settled_count": len(settled_records),
                "wins": sum(
                    1
                    for item in settled_records
                    if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.WIN
                ),
                "losses": sum(
                    1
                    for item in settled_records
                    if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.LOSS
                ),
                "skipped": sum(
                    1
                    for item in settled_records
                    if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.SKIPPED
                ),
                "stage_timings_ms": rounded_timings,
                "counters": dict(counters),
            }
            self._persist_artifact(effective_run_id, "pipeline_summaries", failed_summary_payload)
            self._upsert_run_state(effective_run_id, failed_summary_payload)
            self._emit_stage(
                "pipeline_end",
                run_id=effective_run_id,
                status="failed",
                failed_stage=current_stage,
                error=str(exc),
                stage_timings_ms=rounded_timings,
                counters=dict(counters),
            )
            self._emit_stage(
                "pipeline_critical_failure",
                run_id=effective_run_id,
                failed_stage=current_stage,
                error=str(exc),
            )
            self._trigger_critical_alert(
                "pipeline_critical_failure",
                {
                    "run_id": effective_run_id,
                    "correlation_id": effective_run_id,
                    "failed_stage": current_stage,
                    "error": str(exc),
                },
            )
            raise

        finished_at = datetime.now(UTC)
        self._add_stage_timing(stage_timings_ms, "total", self._elapsed_ms(run_started_perf))
        rounded_timings = self._rounded_stage_timings(stage_timings_ms)
        settled_records = [item for item in records if item.settlement is not None]
        summary = PipelineSummary(
            run_id=effective_run_id,
            correlation_id=effective_run_id,
            started_at=started_at,
            finished_at=finished_at,
            total_markets=len(markets),
            candidates=len(candidates),
            executed_count=sum(1 for item in records if item.execution.status == ExecutionStatus.FILLED),
            settled_count=len(settled_records),
            wins=sum(
                1
                for item in settled_records
                if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.WIN
            ),
            losses=sum(
                1
                for item in settled_records
                if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.LOSS
            ),
            skipped=sum(
                1
                for item in settled_records
                if item.settlement is not None and item.settlement.outcome_classification == OutcomeClassification.SKIPPED
            ),
            stage_timings_ms=rounded_timings,
            counters=dict(counters),
            records=tuple(records),
        )
        self._persist_artifact(
            effective_run_id,
            "pipeline_summaries",
            {
                "run_id": summary.run_id,
                "correlation_id": summary.correlation_id,
                "status": "success",
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                "total_markets": summary.total_markets,
                "candidates": summary.candidates,
                "executed_count": summary.executed_count,
                "settled_count": summary.settled_count,
                "wins": summary.wins,
                "losses": summary.losses,
                "skipped": summary.skipped,
                "stage_timings_ms": dict(summary.stage_timings_ms),
                "counters": dict(summary.counters),
            },
        )
        self._upsert_run_state(
            effective_run_id,
            {
                "run_id": summary.run_id,
                "correlation_id": summary.correlation_id,
                "status": "success",
                "started_at": summary.started_at.isoformat(),
                "finished_at": summary.finished_at.isoformat(),
                "total_markets": summary.total_markets,
                "candidates": summary.candidates,
                "executed_count": summary.executed_count,
                "settled_count": summary.settled_count,
                "wins": summary.wins,
                "losses": summary.losses,
                "skipped": summary.skipped,
                "stage_timings_ms": dict(summary.stage_timings_ms),
                "counters": dict(summary.counters),
            },
        )
        self._emit_stage(
            "pipeline_end",
            run_id=summary.run_id,
            correlation_id=summary.correlation_id,
            status="success",
            total_markets=summary.total_markets,
            candidates=summary.candidates,
            executed=summary.executed_count,
            settled=summary.settled_count,
            wins=summary.wins,
            losses=summary.losses,
            skipped=summary.skipped,
            stage_timings_ms=summary.stage_timings_ms,
            counters=summary.counters,
        )
        return summary

    @staticmethod
    def _default_resolution(market: MarketSnapshot) -> bool:
        checksum = sum(ord(char) for char in market.market_id)
        pivot = int(round(market.yes_price * 1000))
        return ((checksum + pivot) % 2) == 0

    @staticmethod
    def _build_run_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"dryrun-{stamp}"

    def _emit_stage(self, event: str, **payload: object) -> None:
        event_payload = dict(payload)
        run_id = event_payload.get("run_id")
        if isinstance(run_id, str) and run_id and "correlation_id" not in event_payload:
            event_payload["correlation_id"] = run_id
        logger.info(event, extra={"event": event, **event_payload})
        if self.persistence and isinstance(run_id, str):
            self.persistence.write_run_event(run_id, event, event_payload)

    def _persist_artifact(self, run_id: str, artifact_type: str, payload: dict[str, object]) -> None:
        if self.persistence:
            self.persistence.write_artifact(run_id, artifact_type, payload)

    def _upsert_run_state(self, run_id: str, payload: Mapping[str, object]) -> None:
        if self.run_repo is None:
            return
        try:
            self.run_repo.upsert_run(run_id, payload)
        except Exception:
            logger.exception("run_repo_upsert_failed", extra={"event": "run_repo_upsert_failed", "run_id": run_id})

    def _persist_transaction_intent(self, run_id: str, intent: object) -> None:
        if self.transaction_intent_repo is None:
            return
        if not hasattr(intent, "market_id"):
            return
        try:
            self.transaction_intent_repo.upsert_intent(run_id, intent)  # type: ignore[arg-type]
        except Exception:
            logger.exception(
                "transaction_intent_repo_write_failed",
                extra={"event": "transaction_intent_repo_write_failed", "run_id": run_id},
            )

    def _persist_transaction_attempt(self, run_id: str, attempt: object) -> None:
        if self.transaction_attempt_repo is None:
            return
        if not hasattr(attempt, "intent_id"):
            return
        try:
            self.transaction_attempt_repo.append_attempt(run_id, attempt)  # type: ignore[arg-type]
        except Exception:
            logger.exception(
                "transaction_attempt_repo_write_failed",
                extra={"event": "transaction_attempt_repo_write_failed", "run_id": run_id},
            )

    def _persist_transaction_receipt(self, run_id: str, receipt: ExecutionResult) -> None:
        if self.transaction_receipt_repo is None:
            return
        try:
            self.transaction_receipt_repo.append_receipt(run_id, receipt)
        except Exception:
            logger.exception(
                "transaction_receipt_repo_write_failed",
                extra={"event": "transaction_receipt_repo_write_failed", "run_id": run_id},
            )

    def _trigger_critical_alert(self, event_type: str, payload: dict[str, object]) -> None:
        if self.alert_hook is None:
            return
        try:
            self.alert_hook(event_type, payload)
        except Exception as exc:
            self._emit_stage(
                "critical_alert_hook_failed",
                run_id=str(payload.get("run_id", "")),
                alert_event=event_type,
                error=str(exc),
            )

    @staticmethod
    def _elapsed_ms(started_perf: float) -> float:
        return max((perf_counter() - started_perf) * 1000.0, 0.0)

    @staticmethod
    def _add_stage_timing(timings: dict[str, float], stage: str, duration_ms: float) -> None:
        timings[stage] = timings.get(stage, 0.0) + max(duration_ms, 0.0)

    @staticmethod
    def _rounded_stage_timings(timings: Mapping[str, float]) -> dict[str, float]:
        return {key: round(float(value), 3) for key, value in sorted(timings.items())}

    @staticmethod
    def _as_non_negative_int(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(value, 0)
        if isinstance(value, float):
            return max(int(value), 0)
        if isinstance(value, str):
            text = value.strip()
            if text:
                try:
                    return max(int(float(text)), 0)
                except ValueError:
                    return 0
        return 0

    @staticmethod
    def _count_matching_reasons(reasons: Sequence[str], *, token: str) -> int:
        token_text = token.strip()
        if not token_text:
            return 0
        return sum(1 for reason in reasons if token_text in reason)
