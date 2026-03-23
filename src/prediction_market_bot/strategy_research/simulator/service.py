from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.strategy_research.benchmarking import (
    BASELINE_NAMES,
    BaselinePrediction,
    BaselinePredictorSuite,
    LabelRecord,
    build_walk_forward_folds,
    fit_training_stats,
    temporal_leakage_errors,
)
from prediction_market_bot.strategy_research.benchmarking.models import clamp

from .models import (
    CalibrationCohort,
    DecisionSimulationRecord,
    FoldSimulationSummary,
    SimulationAssumptions,
    SimulationThresholds,
    StrategyReportSummary,
    StrategySimulationMetrics,
    WalkForwardSimulationSummary,
)


@dataclass(slots=True, frozen=True)
class StrategySimulationLayout:
    dataset_id: str
    dataset_root: Path
    derived_dir: Path
    labels_path: Path
    strategy_runs_dir: Path
    latest_run_path: Path
    strategy_reports_dir: Path

    @classmethod
    def from_base(
        cls,
        *,
        base_dir: Path,
        dataset_id: str,
        labels_path: Path | None = None,
    ) -> "StrategySimulationLayout":
        dataset_root = base_dir / dataset_id
        derived_dir = dataset_root / "derived"
        effective_labels_path = labels_path if labels_path is not None else (derived_dir / "labels.jsonl")
        return cls(
            dataset_id=dataset_id,
            dataset_root=dataset_root,
            derived_dir=derived_dir,
            labels_path=effective_labels_path,
            strategy_runs_dir=derived_dir / "strategy_walk_forward_runs",
            latest_run_path=derived_dir / "latest_strategy_walk_forward_run.json",
            strategy_reports_dir=derived_dir / "strategy_reports",
        )


@dataclass(slots=True)
class _OpenPosition:
    decision_index: int
    resolved_at_utc: datetime
    selected_side: str
    label_yes: int
    shares: float
    stake_usd: float
    fee_usd: float
    selected_market_price: float


@dataclass(slots=True)
class _MutableDecision:
    row: LabelRecord
    prediction: BaselinePrediction
    fold_index: int
    split_name: str
    approved: bool
    approval_reason: str
    stake_usd: float = 0.0
    fill_price: float = 0.0
    fee_usd: float = 0.0
    bankroll_before_usd: float = 0.0
    bankroll_after_open_usd: float = 0.0
    settled_at_utc: datetime | None = None
    payout_usd: float = 0.0
    pnl_usd: float = 0.0
    realized_edge_probability: float = 0.0
    bankroll_after_settlement_usd: float | None = None

    def to_immutable(self) -> DecisionSimulationRecord:
        return DecisionSimulationRecord(
            row_id=self.row.row_id,
            market_id=self.row.market_id,
            event_id=self.row.event_id,
            fold_index=self.fold_index,
            split_name=self.split_name,
            decision_timestamp_utc=self.row.decision_timestamp_utc,
            resolved_at_utc=self.row.resolved_at_utc,
            predicted_yes_prob=round(self.prediction.predicted_yes_prob, 8),
            market_yes_prob_at_decision=round(self.row.market_yes_prob_at_decision, 8),
            selected_side=self.prediction.selected_side,
            confidence=round(self.prediction.confidence, 8),
            expected_edge_probability=round(self.prediction.edge, 8),
            label_yes=self.row.label_yes,
            approved=self.approved,
            approval_reason=self.approval_reason,
            stake_usd=round(self.stake_usd, 8),
            fill_price=round(self.fill_price, 8),
            fee_usd=round(self.fee_usd, 8),
            bankroll_before_usd=round(self.bankroll_before_usd, 8),
            bankroll_after_open_usd=round(self.bankroll_after_open_usd, 8),
            settled_at_utc=self.settled_at_utc,
            payout_usd=round(self.payout_usd, 8),
            pnl_usd=round(self.pnl_usd, 8),
            realized_edge_probability=round(self.realized_edge_probability, 8),
            bankroll_after_settlement_usd=(
                round(self.bankroll_after_settlement_usd, 8)
                if self.bankroll_after_settlement_usd is not None
                else None
            ),
        )


class WalkForwardStrategyService:
    def __init__(
        self,
        *,
        historical_base_dir: Path,
        default_dataset_id: str,
        prediction_settings: PredictionSettings,
    ) -> None:
        self.historical_base_dir = historical_base_dir
        self.default_dataset_id = default_dataset_id
        self.prediction_settings = prediction_settings

    def run_walk_forward(
        self,
        *,
        dataset_id: str | None = None,
        labels_path: Path | None = None,
        baseline_name: str = "heuristic_prediction_agent",
        eval_split: str = "test",
        train_days: int = 120,
        validation_days: int = 30,
        test_days: int = 30,
        step_days: int = 30,
        max_folds: int = 0,
        min_confidence: float | None = None,
        min_edge_bps: int | None = None,
        initial_bankroll_usd: float = 10_000.0,
        base_position_pct: float = 0.02,
        max_position_pct: float = 0.05,
        min_stake_usd: float = 25.0,
        fee_bps: int = 20,
        slippage_bps: int = 10,
    ) -> WalkForwardSimulationSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = StrategySimulationLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=labels_path,
        )
        rows = self._read_labels(layout.labels_path)
        if not rows:
            raise ValueError(f"No labels found at {layout.labels_path}. Run build-labels first.")
        normalized_baseline = baseline_name.strip().lower()
        if normalized_baseline not in BASELINE_NAMES:
            allowed = ", ".join(BASELINE_NAMES)
            raise ValueError(f"Unsupported baseline_name={baseline_name!r}. Allowed values: {allowed}")
        normalized_eval_split = eval_split.strip().lower()
        if normalized_eval_split not in {"validation", "test"}:
            raise ValueError("eval_split must be one of: validation, test")

        folds = build_walk_forward_folds(
            rows,
            train_days=max(train_days, 1),
            validation_days=max(validation_days, 1),
            test_days=max(test_days, 1),
            step_days=max(step_days, 1),
            max_folds=max(max_folds, 0),
        )
        if not folds:
            raise ValueError("No walk-forward folds produced. Check label coverage and split window parameters.")

        thresholds = SimulationThresholds(
            min_confidence=min_confidence if min_confidence is not None else self.prediction_settings.min_confidence,
            min_edge_probability=(
                (min_edge_bps if min_edge_bps is not None else self.prediction_settings.min_edge_bps) / 10_000.0
            ),
        )
        assumptions = SimulationAssumptions(
            initial_bankroll_usd=max(initial_bankroll_usd, 1.0),
            base_position_pct=clamp(base_position_pct, lower=0.0001, upper=1.0),
            max_position_pct=clamp(max_position_pct, lower=0.0001, upper=1.0),
            min_stake_usd=max(min_stake_usd, 0.0),
            fee_bps=max(fee_bps, 0),
            slippage_bps=max(slippage_bps, 0),
        )
        if assumptions.base_position_pct > assumptions.max_position_pct:
            raise ValueError("base_position_pct cannot be greater than max_position_pct")

        suite = BaselinePredictorSuite(self.prediction_settings)
        fold_summaries: list[FoldSimulationSummary] = []
        warnings: list[str] = []
        for fold in folds:
            leakage_errors = temporal_leakage_errors(fold)
            if leakage_errors:
                raise ValueError("; ".join(leakage_errors))
            eval_rows = fold.validation.rows if normalized_eval_split == "validation" else fold.test.rows
            training_stats = fit_training_stats(list(fold.train.rows))
            
            def predictor(row: LabelRecord) -> BaselinePrediction:
                return suite.predict_all(row, training_stats=training_stats)[normalized_baseline]

            fold_summary = self.simulate_fold(
                fold_index=fold.fold_index,
                split_name=normalized_eval_split,
                train_rows=fold.train.rows,
                eval_rows=eval_rows,
                predict_row=predictor,
                thresholds=thresholds,
                assumptions=assumptions,
            )
            fold_summaries.append(fold_summary)
            warnings.extend(fold_summary.warnings)

        aggregate = self._aggregate_metrics(
            fold_summaries=fold_summaries,
            initial_bankroll_usd=assumptions.initial_bankroll_usd,
        )
        cohorts = self._build_calibration_cohorts(
            records=[row for fold in fold_summaries for row in fold.decisions],
        )
        duplicate_rows = self._count_duplicate_rows([row for fold in fold_summaries for row in fold.decisions])
        if duplicate_rows > 0:
            warnings.append(
                f"walk_forward_overlap_detected duplicate_eval_rows={duplicate_rows}; "
                "consider increasing step_days to avoid window overlap."
            )

        run_id = f"walk-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        layout.strategy_runs_dir.mkdir(parents=True, exist_ok=True)
        run_path = layout.strategy_runs_dir / f"{run_id}.json"
        summary = WalkForwardSimulationSummary(
            dataset_id=effective_dataset_id,
            run_id=run_id,
            run_path=run_path,
            baseline_name=normalized_baseline,
            eval_split=normalized_eval_split,
            thresholds=thresholds,
            assumptions=assumptions,
            folds=tuple(fold_summaries),
            aggregate_metrics=aggregate,
            calibration_by_cohort=cohorts,
            warnings=tuple(warnings),
        )
        payload = summary.to_dict()
        run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        layout.latest_run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return summary

    def generate_strategy_report(
        self,
        *,
        dataset_id: str | None = None,
        run_id: str | None = None,
        output_path: Path | None = None,
    ) -> StrategyReportSummary:
        effective_dataset_id = (dataset_id or self.default_dataset_id).strip() or self.default_dataset_id
        layout = StrategySimulationLayout.from_base(
            base_dir=self.historical_base_dir,
            dataset_id=effective_dataset_id,
            labels_path=None,
        )
        payload = self._load_run_payload(layout=layout, run_id=run_id)
        resolved_run_id = str(payload.get("run_id") or "").strip()
        if not resolved_run_id:
            raise ValueError("Invalid strategy run payload: missing run_id")
        report = self._render_report_markdown(payload)
        resolved_output = (
            output_path
            if output_path is not None
            else (layout.strategy_reports_dir / f"{resolved_run_id}.md")
        )
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(report, encoding="utf-8")
        return StrategyReportSummary(
            dataset_id=effective_dataset_id,
            run_id=resolved_run_id,
            report_path=resolved_output,
        )

    def simulate_fold(
        self,
        *,
        fold_index: int,
        split_name: str,
        train_rows: Sequence[LabelRecord],
        eval_rows: Sequence[LabelRecord],
        predict_row: Callable[[LabelRecord], BaselinePrediction],
        thresholds: SimulationThresholds,
        assumptions: SimulationAssumptions,
    ) -> FoldSimulationSummary:
        ordered_eval = tuple(sorted(eval_rows, key=lambda row: (row.decision_timestamp_utc, row.market_id)))
        train_rows = tuple(sorted(train_rows, key=lambda row: (row.decision_timestamp_utc, row.market_id)))
        if train_rows and ordered_eval:
            train_end = train_rows[-1].decision_timestamp_utc
            eval_start = ordered_eval[0].decision_timestamp_utc
            if eval_start <= train_end:
                raise ValueError(
                    f"fold={fold_index}: eval split starts at {eval_start.isoformat()} "
                    f"which is not strictly after train end {train_end.isoformat()}"
                )

        mutable_decisions: list[_MutableDecision] = []
        open_positions: list[_OpenPosition] = []
        warnings: list[str] = []
        cash = assumptions.initial_bankroll_usd
        peak_equity = assumptions.initial_bankroll_usd
        max_drawdown = 0.0
        slippage_probability = assumptions.slippage_bps / 10_000.0
        fee_rate = assumptions.fee_bps / 10_000.0

        def equity() -> float:
            return cash + sum(position.stake_usd for position in open_positions)

        def update_drawdown() -> None:
            nonlocal peak_equity, max_drawdown
            current_equity = equity()
            if current_equity > peak_equity:
                peak_equity = current_equity
            if peak_equity > 0.0:
                drawdown = (peak_equity - current_equity) / peak_equity
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        def settle_up_to(timestamp: datetime) -> None:
            nonlocal cash
            pending = sorted(open_positions, key=lambda row: (row.resolved_at_utc, row.decision_index))
            for position in pending:
                if position.resolved_at_utc > timestamp:
                    continue
                open_positions.remove(position)
                decision = mutable_decisions[position.decision_index]
                won = (
                    (position.selected_side == "YES" and position.label_yes == 1)
                    or (position.selected_side == "NO" and position.label_yes == 0)
                )
                payout = position.shares if won else 0.0
                cash += payout
                decision.settled_at_utc = position.resolved_at_utc
                decision.payout_usd = payout
                decision.pnl_usd = payout - position.stake_usd - position.fee_usd
                decision.realized_edge_probability = (1.0 if won else 0.0) - position.selected_market_price
                decision.bankroll_after_settlement_usd = equity()
                update_drawdown()

        for row in ordered_eval:
            settle_up_to(row.decision_timestamp_utc)
            prediction = predict_row(row)
            approved = prediction.confidence >= thresholds.min_confidence and prediction.edge >= thresholds.min_edge_probability
            approval_reason = "approved"
            bankroll_before = equity()
            stake_usd = 0.0
            fee_usd = 0.0
            fill_price = 0.0
            bankroll_after_open = bankroll_before

            if approved:
                edge_multiplier = clamp(
                    prediction.edge / max(thresholds.min_edge_probability, 1e-6),
                    lower=0.5,
                    upper=3.0,
                )
                target_pct = assumptions.base_position_pct * prediction.confidence * edge_multiplier
                target_pct = clamp(target_pct, lower=0.0, upper=assumptions.max_position_pct)
                stake_usd = min(cash * target_pct, cash * assumptions.max_position_pct)
                if stake_usd < assumptions.min_stake_usd:
                    approved = False
                    approval_reason = "stake_below_minimum"
                    stake_usd = 0.0
                else:
                    fee_usd = stake_usd * fee_rate
                    total_debit = stake_usd + fee_usd
                    if total_debit > cash:
                        stake_usd = cash / (1.0 + fee_rate)
                        fee_usd = stake_usd * fee_rate
                        total_debit = stake_usd + fee_usd
                    if total_debit > cash or stake_usd < assumptions.min_stake_usd:
                        approved = False
                        approval_reason = "insufficient_cash"
                        stake_usd = 0.0
                        fee_usd = 0.0
                    else:
                        fill_price = clamp(
                            prediction.selected_market_price + slippage_probability,
                            lower=0.001,
                            upper=0.999,
                        )
                        shares = stake_usd / fill_price
                        resolved_at = row.resolved_at_utc
                        if resolved_at is None:
                            resolved_at = row.decision_timestamp_utc
                            warnings.append(
                                f"fold={fold_index} row={row.row_id} missing resolved_at_utc; "
                                "settled immediately at decision timestamp."
                            )
                        cash -= total_debit
                        open_positions.append(
                            _OpenPosition(
                                decision_index=len(mutable_decisions),
                                resolved_at_utc=resolved_at,
                                selected_side=prediction.selected_side,
                                label_yes=row.label_yes,
                                shares=shares,
                                stake_usd=stake_usd,
                                fee_usd=fee_usd,
                                selected_market_price=prediction.selected_market_price,
                            )
                        )
                        bankroll_after_open = equity()
                        update_drawdown()
            else:
                has_conf = prediction.confidence >= thresholds.min_confidence
                has_edge = prediction.edge >= thresholds.min_edge_probability
                if not has_conf and not has_edge:
                    approval_reason = "below_confidence_and_edge_thresholds"
                elif not has_conf:
                    approval_reason = "below_confidence_threshold"
                else:
                    approval_reason = "below_edge_threshold"

            mutable_decisions.append(
                _MutableDecision(
                    row=row,
                    prediction=prediction,
                    fold_index=fold_index,
                    split_name=split_name,
                    approved=approved,
                    approval_reason=approval_reason,
                    stake_usd=stake_usd,
                    fill_price=fill_price,
                    fee_usd=fee_usd,
                    bankroll_before_usd=bankroll_before,
                    bankroll_after_open_usd=bankroll_after_open,
                )
            )

        settle_up_to(datetime.max.replace(tzinfo=UTC))
        metrics = self._metrics_from_decisions(
            decisions=mutable_decisions,
            initial_bankroll_usd=assumptions.initial_bankroll_usd,
            final_bankroll_usd=equity(),
            max_drawdown=max_drawdown,
        )
        return FoldSimulationSummary(
            fold_index=fold_index,
            split_name=split_name,
            train_start_utc=train_rows[0].decision_timestamp_utc if train_rows else None,
            train_end_utc=train_rows[-1].decision_timestamp_utc if train_rows else None,
            eval_start_utc=ordered_eval[0].decision_timestamp_utc if ordered_eval else None,
            eval_end_utc=ordered_eval[-1].decision_timestamp_utc if ordered_eval else None,
            metrics=metrics,
            decisions=tuple(row.to_immutable() for row in mutable_decisions),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _aggregate_metrics(
        *,
        fold_summaries: Sequence[FoldSimulationSummary],
        initial_bankroll_usd: float,
    ) -> StrategySimulationMetrics:
        records: list[DecisionSimulationRecord] = [row for fold in fold_summaries for row in fold.decisions]
        initial_total = initial_bankroll_usd * max(len(fold_summaries), 1)
        pnl_total = sum(fold.metrics.pnl_usd for fold in fold_summaries)
        max_drawdown = max((fold.metrics.max_drawdown_pct for fold in fold_summaries), default=0.0)
        final_bankroll = initial_total + pnl_total
        if not records:
            return StrategySimulationMetrics(
                total_predictions=0,
                approved_predictions=0,
                settled_trades=0,
                approval_rate=0.0,
                total_stake_usd=0.0,
                turnover=0.0,
                pnl_usd=0.0,
                roi=0.0,
                final_bankroll_usd=final_bankroll,
                max_drawdown_pct=max_drawdown,
                expected_edge_total=0.0,
                expected_edge_mean=0.0,
                realized_edge_total=0.0,
                realized_edge_mean=0.0,
                edge_capture_ratio=0.0,
                brier_score=0.0,
                log_loss=0.0,
            )
        total_predictions = len(records)
        approved_records = [row for row in records if row.approved]
        settled_records = [row for row in approved_records if row.settled_at_utc is not None]
        approved_count = len(approved_records)
        settled_count = len(settled_records)
        total_stake = sum(row.stake_usd for row in approved_records)
        expected_edge_total = sum(row.expected_edge_probability for row in approved_records)
        realized_edge_total = sum(row.realized_edge_probability for row in settled_records)
        expected_edge_mean = expected_edge_total / approved_count if approved_count > 0 else 0.0
        realized_edge_mean = realized_edge_total / approved_count if approved_count > 0 else 0.0
        edge_capture_ratio = realized_edge_total / expected_edge_total if abs(expected_edge_total) > 1e-8 else 0.0
        eps = 1e-6
        brier_sum = 0.0
        log_loss_sum = 0.0
        for row in records:
            prob = clamp(row.predicted_yes_prob, lower=eps, upper=1.0 - eps)
            label = row.label_yes
            brier_sum += (prob - label) ** 2
            log_loss_sum += -((label * math.log(prob)) + ((1 - label) * math.log(1.0 - prob)))
        return StrategySimulationMetrics(
            total_predictions=total_predictions,
            approved_predictions=approved_count,
            settled_trades=settled_count,
            approval_rate=approved_count / total_predictions if total_predictions > 0 else 0.0,
            total_stake_usd=total_stake,
            turnover=total_stake / initial_total if initial_total > 0.0 else 0.0,
            pnl_usd=pnl_total,
            roi=pnl_total / initial_total if initial_total > 0.0 else 0.0,
            final_bankroll_usd=final_bankroll,
            max_drawdown_pct=max_drawdown,
            expected_edge_total=expected_edge_total,
            expected_edge_mean=expected_edge_mean,
            realized_edge_total=realized_edge_total,
            realized_edge_mean=realized_edge_mean,
            edge_capture_ratio=edge_capture_ratio,
            brier_score=brier_sum / total_predictions,
            log_loss=log_loss_sum / total_predictions,
        )

    @staticmethod
    def _metrics_from_decisions(
        *,
        decisions: Sequence[_MutableDecision],
        initial_bankroll_usd: float,
        final_bankroll_usd: float,
        max_drawdown: float,
    ) -> StrategySimulationMetrics:
        if not decisions:
            return StrategySimulationMetrics(
                total_predictions=0,
                approved_predictions=0,
                settled_trades=0,
                approval_rate=0.0,
                total_stake_usd=0.0,
                turnover=0.0,
                pnl_usd=0.0,
                roi=0.0,
                final_bankroll_usd=final_bankroll_usd,
                max_drawdown_pct=max_drawdown,
                expected_edge_total=0.0,
                expected_edge_mean=0.0,
                realized_edge_total=0.0,
                realized_edge_mean=0.0,
                edge_capture_ratio=0.0,
                brier_score=0.0,
                log_loss=0.0,
            )
        records = [row.to_immutable() for row in decisions]
        approved = [row for row in records if row.approved]
        settled = [row for row in approved if row.settled_at_utc is not None]
        approved_count = len(approved)
        total_predictions = len(records)
        total_stake = sum(row.stake_usd for row in approved)
        pnl = sum(row.pnl_usd for row in approved)
        expected_edge_total = sum(row.expected_edge_probability for row in approved)
        realized_edge_total = sum(row.realized_edge_probability for row in settled)
        expected_edge_mean = expected_edge_total / approved_count if approved_count > 0 else 0.0
        realized_edge_mean = realized_edge_total / approved_count if approved_count > 0 else 0.0
        edge_capture_ratio = realized_edge_total / expected_edge_total if abs(expected_edge_total) > 1e-8 else 0.0
        eps = 1e-6
        brier_sum = 0.0
        log_loss_sum = 0.0
        for row in records:
            prob = clamp(row.predicted_yes_prob, lower=eps, upper=1.0 - eps)
            label = row.label_yes
            brier_sum += (prob - label) ** 2
            log_loss_sum += -((label * math.log(prob)) + ((1 - label) * math.log(1.0 - prob)))
        return StrategySimulationMetrics(
            total_predictions=total_predictions,
            approved_predictions=approved_count,
            settled_trades=len(settled),
            approval_rate=approved_count / total_predictions if total_predictions > 0 else 0.0,
            total_stake_usd=total_stake,
            turnover=total_stake / initial_bankroll_usd if initial_bankroll_usd > 0.0 else 0.0,
            pnl_usd=pnl,
            roi=pnl / initial_bankroll_usd if initial_bankroll_usd > 0.0 else 0.0,
            final_bankroll_usd=final_bankroll_usd,
            max_drawdown_pct=max_drawdown,
            expected_edge_total=expected_edge_total,
            expected_edge_mean=expected_edge_mean,
            realized_edge_total=realized_edge_total,
            realized_edge_mean=realized_edge_mean,
            edge_capture_ratio=edge_capture_ratio,
            brier_score=brier_sum / total_predictions,
            log_loss=log_loss_sum / total_predictions,
        )

    @staticmethod
    def _build_calibration_cohorts(records: Sequence[DecisionSimulationRecord]) -> tuple[CalibrationCohort, ...]:
        buckets: dict[int, list[DecisionSimulationRecord]] = {index: [] for index in range(10)}
        for row in records:
            bucket = min(int(row.predicted_yes_prob * 10), 9)
            buckets[bucket].append(row)
        cohorts: list[CalibrationCohort] = []
        for index in range(10):
            rows = buckets[index]
            if not rows:
                continue
            lower = index / 10.0
            upper = (index + 1) / 10.0
            mean_pred = sum(row.predicted_yes_prob for row in rows) / len(rows)
            observed = sum(row.label_yes for row in rows) / len(rows)
            cohorts.append(
                CalibrationCohort(
                    bucket=f"[{lower:.1f},{upper:.1f})" if index < 9 else "[0.9,1.0]",
                    sample_count=len(rows),
                    mean_predicted_yes_prob=mean_pred,
                    observed_yes_rate=observed,
                )
            )
        return tuple(cohorts)

    @staticmethod
    def _count_duplicate_rows(records: Sequence[DecisionSimulationRecord]) -> int:
        seen: set[str] = set()
        duplicates = 0
        for row in records:
            if row.row_id in seen:
                duplicates += 1
                continue
            seen.add(row.row_id)
        return duplicates

    @staticmethod
    def _render_report_markdown(payload: Mapping[str, Any]) -> str:
        run_id = str(payload.get("run_id") or "")
        dataset_id = str(payload.get("dataset_id") or "")
        baseline_name = str(payload.get("baseline_name") or "")
        eval_split = str(payload.get("eval_split") or "")
        aggregate = payload.get("aggregate_metrics")
        aggregate_map = dict(aggregate) if isinstance(aggregate, Mapping) else {}
        cohorts = payload.get("calibration_by_cohort")
        cohort_rows = cohorts if isinstance(cohorts, list) else []
        folds = payload.get("folds")
        fold_rows = folds if isinstance(folds, list) else []
        lines = [
            f"# Strategy Walk-Forward Report ({run_id})",
            "",
            "## Run Context",
            f"- dataset_id: `{dataset_id}`",
            f"- baseline_name: `{baseline_name}`",
            f"- eval_split: `{eval_split}`",
            "",
            "## Aggregate Metrics",
            f"- ROI: {float(aggregate_map.get('roi', 0.0)):.6f}",
            f"- PnL USD: {float(aggregate_map.get('pnl_usd', 0.0)):.6f}",
            f"- Max drawdown: {float(aggregate_map.get('max_drawdown_pct', 0.0)):.6f}",
            f"- Turnover: {float(aggregate_map.get('turnover', 0.0)):.6f}",
            f"- Approval rate: {float(aggregate_map.get('approval_rate', 0.0)):.6f}",
            f"- Expected edge total: {float(aggregate_map.get('expected_edge_total', 0.0)):.6f}",
            f"- Realized edge total: {float(aggregate_map.get('realized_edge_total', 0.0)):.6f}",
            f"- Edge capture ratio: {float(aggregate_map.get('edge_capture_ratio', 0.0)):.6f}",
            f"- Brier score: {float(aggregate_map.get('brier_score', 0.0)):.6f}",
            f"- Log loss: {float(aggregate_map.get('log_loss', 0.0)):.6f}",
            "",
            "## Walk-Forward Folds",
            "",
            "| Fold | Eval Start | Eval End | ROI | PnL USD | Approval Rate | Max Drawdown |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
        for fold in fold_rows:
            if not isinstance(fold, Mapping):
                continue
            metrics = fold.get("metrics")
            metrics_map = dict(metrics) if isinstance(metrics, Mapping) else {}
            lines.append(
                "| "
                f"{int(fold.get('fold_index') or 0)} | "
                f"{str(fold.get('eval_start_utc') or '-')} | "
                f"{str(fold.get('eval_end_utc') or '-')} | "
                f"{float(metrics_map.get('roi', 0.0)):.6f} | "
                f"{float(metrics_map.get('pnl_usd', 0.0)):.6f} | "
                f"{float(metrics_map.get('approval_rate', 0.0)):.6f} | "
                f"{float(metrics_map.get('max_drawdown_pct', 0.0)):.6f} |"
            )
        lines.extend(
            [
                "",
                "## Calibration By Cohort",
                "",
                "| Cohort | Samples | Mean Predicted YES | Observed YES Rate |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in cohort_rows:
            if not isinstance(row, Mapping):
                continue
            lines.append(
                "| "
                f"{str(row.get('bucket') or '-')} | "
                f"{int(row.get('sample_count') or 0)} | "
                f"{float(row.get('mean_predicted_yes_prob', 0.0)):.6f} | "
                f"{float(row.get('observed_yes_rate', 0.0)):.6f} |"
            )
        warnings = payload.get("warnings")
        warning_rows = warnings if isinstance(warnings, list) else []
        if warning_rows:
            lines.extend(["", "## Warnings"])
            for warning in warning_rows:
                lines.append(f"- {warning}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _load_run_payload(*, layout: StrategySimulationLayout, run_id: str | None) -> Mapping[str, Any]:
        def load_file(path: Path) -> Mapping[str, Any]:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ValueError(f"Invalid strategy run payload: {path}")
            return raw

        if run_id:
            run_path = layout.strategy_runs_dir / f"{run_id}.json"
            if not run_path.exists():
                raise FileNotFoundError(f"Strategy run not found: {run_path}")
            return load_file(run_path)
        if layout.latest_run_path.exists():
            return load_file(layout.latest_run_path)
        if not layout.strategy_runs_dir.exists():
            raise FileNotFoundError("No strategy walk-forward runs found.")
        run_files = sorted(layout.strategy_runs_dir.glob("walk-*.json"))
        if not run_files:
            raise FileNotFoundError("No strategy walk-forward runs found.")
        return load_file(run_files[-1])

    @staticmethod
    def _read_labels(path: Path) -> list[LabelRecord]:
        if not path.exists():
            return []
        rows: list[LabelRecord] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                raw = json.loads(text)
                if isinstance(raw, dict):
                    rows.append(LabelRecord.from_dict(raw))
        return rows
