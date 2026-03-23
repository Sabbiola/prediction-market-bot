from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping, TypeVar, cast

from prediction_market_bot.services import (
    OperatorControlState,
    build_shadow_scoring_report,
    load_operator_state,
    operator_state_path,
)
from prediction_market_bot.services.model_promotion import build_model_drift_report, resolve_runtime_model_gate_decision
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    DriftAlertResponse,
    DriftSignalResponse,
    IncidentBannerResponse,
    ModelVisibilityResponse,
    PredictionRowResponse,
    PredictionTabResponse,
    RunSelectorResponse,
    ShadowComparisonHistoryRowResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    as_map,
    chart_from_counter,
    chart_from_mapping,
    empty_prediction,
    to_float,
    to_optional_float,
    to_text,
    to_text_tuple,
)

_T = TypeVar("_T")


@dataclass(slots=True)
class PredictionSupportService:
    context: UiRuntimeContext
    queries: UiReadQueryService
    _cache_ttl_sec: float = field(init=False, default=0.0)
    _cache: dict[str, tuple[float, object]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._cache_ttl_sec = max(self.context.settings.performance.ui_poll_cache_ttl_sec, 0.0)

    def clear_cache(self) -> None:
        self._cache.clear()

    def model_visibility(self, *, state: OperatorControlState | None = None) -> ModelVisibilityResponse:
        settings = self.context.settings
        operator_state = state
        if operator_state is None:
            operator_state = load_operator_state(
                operator_state_path(settings.storage.artifacts_dir),
                repository=self.context.operational.operator_control_state,
            )
        decision = resolve_runtime_model_gate_decision(
            settings=settings,
            state=operator_state,
        )
        alt_promoted_modes = {
            item.strip().upper()
            for item in settings.prediction.alt_shadow_promoted_runtime_modes
            if item.strip()
        }
        alt_promoted_active = bool(
            settings.prediction.alt_shadow_enabled
            and settings.prediction.alt_shadow_promoted_enabled
            and settings.runtime.mode.value == "SANDBOX_CHAIN"
            and (not alt_promoted_modes or settings.runtime.mode.value in alt_promoted_modes)
            and decision.allowed
            and decision.effective_engine == "model_v2"
        )
        model_artifact_path = (
            settings.prediction.alt_shadow_model_artifact_path
            if alt_promoted_active and settings.prediction.alt_shadow_model_artifact_path.strip()
            else settings.prediction.model_artifact_path
        )
        calibration_artifact_path = (
            settings.prediction.alt_shadow_calibration_artifact_path
            if alt_promoted_active and settings.prediction.alt_shadow_calibration_artifact_path.strip()
            else settings.prediction.calibration_artifact_path
        )
        artifact_payload = read_json_mapping(Path(model_artifact_path))
        model_name = to_text(artifact_payload.get("model_name")) or "heuristic"
        feature_schema_version = to_text(artifact_payload.get("feature_schema_version")) or (
            settings.prediction.alt_shadow_expected_feature_schema_version
            if alt_promoted_active
            else settings.prediction.model_expected_feature_schema_version
        )
        calibration_version = ""
        calibration_method = ""
        artifact_calibration = as_map(artifact_payload.get("calibration"))
        if artifact_calibration:
            calibration_version = to_text(artifact_calibration.get("calibration_version"))
            calibration_method = to_text(artifact_calibration.get("method"))
        if (not calibration_version or not calibration_method) and calibration_artifact_path.strip():
            calibration_payload = read_json_mapping(Path(calibration_artifact_path))
            calibration_version = calibration_version or to_text(calibration_payload.get("calibration_version"))
            calibration_method = calibration_method or to_text(calibration_payload.get("method"))
        active_model_version = to_text(artifact_payload.get("model_version"))
        if not active_model_version and not alt_promoted_active:
            active_model_version = decision.model_version
        active_source_set: tuple[str, ...] = ()
        effective_engine = decision.effective_engine
        gate_reason = decision.reason
        if alt_promoted_active:
            effective_engine = "model_v2_alt_promoted"
            gate_reason = "model_v2_allowed_alt_promoted_path_active"
            active_source_set = tuple(
                source.strip().lower()
                for source in settings.prediction.alt_shadow_required_source_coverage
                if source.strip()
            )
        return ModelVisibilityResponse(
            runtime_mode=settings.runtime.mode.value,
            requested_engine=decision.requested_engine,
            effective_engine=effective_engine,
            gate_required=decision.gate_required,
            gate_reason=gate_reason,
            active_model_version=active_model_version,
            model_name=model_name,
            feature_schema_version=feature_schema_version,
            calibration_version=calibration_version,
            calibration_method=calibration_method,
            active_source_set=active_source_set,
            promoted_model_version=getattr(operator_state, "model_v2_promoted_model_version", ""),
            promoted_at=getattr(operator_state, "model_v2_promoted_at", ""),
            rollback_active=bool(getattr(operator_state, "model_v2_rollback_active", False)),
            rollback_reason=getattr(operator_state, "model_v2_rollback_reason", ""),
        )

    def shadow_report(self, *, run_id: str) -> object | None:
        run = run_id.strip()
        if not run:
            return None
        cache_key = self._cache_key("shadow_report", run_id=run)

        def _load() -> object | None:
            report = build_shadow_scoring_report(self.context.persistence, run)
            if report.total_rows <= 0:
                return None
            return report

        return self._cached(cache_key, _load)

    def shadow_summary(self, *, run_id: str, report: object | None = None) -> ShadowComparisonHistoryRowResponse | None:
        resolved = report if report is not None else self.shadow_report(run_id=run_id)
        if resolved is None:
            return None
        total_rows = int(getattr(resolved, "total_rows", 0))
        rows_with_model_v2 = int(getattr(resolved, "rows_with_model_v2", 0))
        if total_rows <= 0 and rows_with_model_v2 <= 0:
            return None
        approval_rate = as_map(getattr(resolved, "approval_rate", {}))
        disagreement = as_map(getattr(resolved, "disagreement_buckets", {}))
        return ShadowComparisonHistoryRowResponse(
            run_id=run_id,
            total_rows=total_rows,
            rows_with_model_v2=rows_with_model_v2,
            rows_with_parity_warnings=int(getattr(resolved, "rows_with_parity_warnings", 0)),
            approval_rate_heuristic=to_optional_float(approval_rate.get("heuristic_rate")),
            approval_rate_model_v2=to_optional_float(approval_rate.get("model_v2_rate")),
            approval_rate_delta_model_minus_heuristic=to_optional_float(
                approval_rate.get("approval_rate_delta_model_minus_heuristic")
            ),
            disagreement_buckets=chart_from_mapping(disagreement),
        )

    def shadow_history(self, *, active_run_id: str, limit: int = 8) -> tuple[ShadowComparisonHistoryRowResponse, ...]:
        if limit <= 0:
            return ()
        cache_key = self._cache_key("shadow_history", active_run_id=active_run_id.strip(), limit=str(limit))

        def _load() -> tuple[ShadowComparisonHistoryRowResponse, ...]:
            run_ids = self.queries.run_ids(limit_runs=100)
            ordered: list[str] = []
            if active_run_id.strip():
                ordered.append(active_run_id.strip())
            ordered.extend(reversed(run_ids))
            history: list[ShadowComparisonHistoryRowResponse] = []
            seen: set[str] = set()
            for candidate in ordered:
                rid = candidate.strip()
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                summary = self.shadow_summary(run_id=rid)
                if summary is None:
                    continue
                history.append(summary)
                if len(history) >= limit:
                    break
            return tuple(history)

        return self._cached(cache_key, _load)

    def drift_alert(self, *, run_id: str) -> DriftAlertResponse | None:
        run = run_id.strip()
        if not run:
            return None
        cache_key = self._cache_key("drift_alert", run_id=run)

        def _load() -> DriftAlertResponse | None:
            try:
                report = build_model_drift_report(
                    settings=self.context.settings,
                    persistence=self.context.persistence,
                    run_id=run,
                )
            except Exception:
                return None
            signals = tuple(
                DriftSignalResponse(
                    name=signal.name,
                    status=signal.status,
                    detail=signal.detail,
                    current_value=signal.current_value,
                    reference_value=signal.reference_value,
                    threshold=signal.threshold,
                )
                for signal in report.signals
            )
            return DriftAlertResponse(
                overall_status=report.overall_status,
                current_run_id=report.current_run_id,
                created_at_utc=report.created_at_utc,
                warnings=tuple(report.warnings),
                signals=signals,
            )

        return self._cached(cache_key, _load)

    def alt_promoted_summary(self, *, run_id: str) -> dict[str, object] | None:
        run = run_id.strip()
        if not run:
            return None
        cache_key = self._cache_key("alt_promoted_summary", run_id=run)

        def _load() -> dict[str, object] | None:
            rows = self.queries.artifact_payloads(run, "prediction_alt_comparisons")
            if not rows:
                return None
            disagreement_buckets: dict[str, int] = {}
            enrichment_values: list[float] = []
            model_v2_approved = 0
            alt_approved = 0
            approval_sample = 0
            for payload in rows:
                bucket = to_text(payload.get("disagreement_bucket")) or "unknown"
                disagreement_buckets[bucket] = disagreement_buckets.get(bucket, 0) + 1
                enrichment = to_optional_float(payload.get("enrichment_coverage"))
                if enrichment is not None:
                    enrichment_values.append(enrichment)
                approvals = as_map(payload.get("approvals"))
                model_flag = approvals.get("model_v2")
                alt_flag = approvals.get("alt_llm_promoted")
                if isinstance(model_flag, bool):
                    approval_sample += 1
                    if model_flag:
                        model_v2_approved += 1
                    if isinstance(alt_flag, bool) and alt_flag:
                        alt_approved += 1
            approval_rate: dict[str, float | None] = {
                "model_v2_rate": None,
                "alt_llm_promoted_rate": None,
                "approval_rate_delta_alt_minus_model_v2": None,
            }
            if approval_sample > 0:
                model_rate = model_v2_approved / approval_sample
                alt_rate = alt_approved / approval_sample
                approval_rate = {
                    "model_v2_rate": round(model_rate, 8),
                    "alt_llm_promoted_rate": round(alt_rate, 8),
                    "approval_rate_delta_alt_minus_model_v2": round(alt_rate - model_rate, 8),
                }
            enrichment_coverage = None
            if enrichment_values:
                enrichment_coverage = round(sum(enrichment_values) / len(enrichment_values), 8)
            return {
                "rows_count": len(rows),
                "disagreement_buckets": disagreement_buckets,
                "enrichment_coverage": enrichment_coverage,
                "approval_rate": approval_rate,
            }

        return self._cached(cache_key, _load)

    def _cache_key(self, name: str, **parts: str) -> str:
        tokens = [name.strip().lower()]
        for key in sorted(parts):
            tokens.append(f"{key.strip().lower()}={parts[key].strip()}")
        return "|".join(tokens)

    def _cached(self, cache_key: str, loader: Callable[[], _T]) -> _T:
        if self._cache_ttl_sec <= 0:
            return loader()
        now = monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] >= now:
            return cast(_T, cached[1])
        payload = loader()
        self._cache[cache_key] = (now + self._cache_ttl_sec, payload)
        return payload

    @staticmethod
    def chart_from_shadow_calibration(report: object | None) -> tuple[ChartPointResponse, ...]:
        if report is None:
            return ()
        calibration = as_map(getattr(report, "calibration", {}))
        rows: list[ChartPointResponse] = []
        mapping = (
            ("heuristic_brier", "heuristic_brier"),
            ("model_v2_brier", "model_v2_brier"),
            ("brier_delta_model_minus_heuristic", "brier_delta"),
            ("heuristic_calibration_gap", "heuristic_calibration_gap"),
            ("model_v2_calibration_gap", "model_v2_calibration_gap"),
            ("calibration_gap_delta_model_minus_heuristic", "calibration_gap_delta"),
        )
        for key, label in mapping:
            value = to_optional_float(calibration.get(key))
            if value is None:
                continue
            rows.append(ChartPointResponse(label=label, value=value))
        return tuple(rows)

    @staticmethod
    def chart_from_shadow_approval(report: object | None) -> tuple[ChartPointResponse, ...]:
        if report is None:
            return ()
        approval = as_map(getattr(report, "approval_rate", {}))
        rows: list[ChartPointResponse] = []
        mapping = (
            ("heuristic_rate", "heuristic_rate"),
            ("model_v2_rate", "model_v2_rate"),
            ("approval_rate_delta_model_minus_heuristic", "approval_rate_delta"),
        )
        for key, label in mapping:
            value = to_optional_float(approval.get(key))
            if value is None:
                continue
            rows.append(ChartPointResponse(label=label, value=value))
        return tuple(rows)


def build_prediction_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    support: PredictionSupportService,
    selector: RunSelectorResponse,
) -> PredictionTabResponse:
    run = selector.selected_run_id
    state = load_operator_state(
        operator_state_path(context.settings.storage.artifacts_dir),
        repository=context.operational.operator_control_state,
    )
    model_visibility = support.model_visibility(state=state)
    drift_alert = support.drift_alert(run_id=run)
    shadow_report = support.shadow_report(run_id=run) if run else None
    shadow_summary = support.shadow_summary(run_id=run, report=shadow_report) if run else None
    alt_promoted_summary = support.alt_promoted_summary(run_id=run) if run else None
    shadow_history = support.shadow_history(active_run_id=run)
    calibration_summary = (
        support.chart_from_shadow_calibration(shadow_report)
        if shadow_report is not None
        else ()
    )
    approval_rate_summary: tuple[ChartPointResponse, ...] = (
        support.chart_from_shadow_approval(shadow_report)
        if shadow_report is not None
        else ()
    )
    disagreement_buckets = shadow_summary.disagreement_buckets if shadow_summary is not None else ()
    enrichment_coverage: float | None = None
    disagreement_vs_baseline: tuple[ChartPointResponse, ...] = ()
    if shadow_summary is None and alt_promoted_summary is not None:
        disagreement_buckets = chart_from_mapping(alt_promoted_summary.get("disagreement_buckets"))
        disagreement_vs_baseline = disagreement_buckets
        enrichment_coverage = to_optional_float(alt_promoted_summary.get("enrichment_coverage"))
        approval_rate_summary = chart_from_mapping(
            alt_promoted_summary.get("approval_rate"),
            preferred_order=("model_v2_rate", "alt_llm_promoted_rate", "approval_rate_delta_alt_minus_model_v2"),
        )
    parity_warning_rows = queries.artifact_payloads(run, "prediction_parity_warnings") if run else []
    parity_warning_count = len(parity_warning_rows)
    anomalies: list[IncidentBannerResponse] = []

    if parity_warning_count > 0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="prediction_parity_warnings_detected",
                title="Prediction parity warnings detected",
                detail=f"parity_warning_rows={parity_warning_count}",
                recommendation="Inspect shadow parity warnings before promotion/approval decisions.",
            )
        )
    if drift_alert is not None and drift_alert.overall_status in {"warning", "critical"}:
        anomalies.append(
            IncidentBannerResponse(
                level="danger" if drift_alert.overall_status == "critical" else "warning",
                code="prediction_drift_alert_active",
                title="Prediction drift alert is active",
                detail=f"overall_status={drift_alert.overall_status} signals={len(drift_alert.signals)}",
                recommendation="Review drift signal details and avoid unsafe model promotion actions.",
            )
        )

    if not run:
        return empty_prediction(
            selector,
            "no_run_selected",
            model_visibility=model_visibility,
            shadow_history=shadow_history,
            drift_alert=drift_alert,
        )
    predictions = queries.artifact_payloads(run, "prediction_results")
    rows: list[PredictionRowResponse] = []
    side_counter: dict[str, int] = {}
    confidence_sum = 0.0
    edge_sum_bps = 0.0
    probability_gap_sum = 0.0
    for payload in predictions:
        side = to_text(payload.get("selected_side")) or "n/a"
        confidence = to_float(payload.get("confidence"))
        edge_bps = to_float(payload.get("edge")) * 10_000.0
        market_yes_prob = to_float(payload.get("market_yes_prob"))
        fair_yes_prob = to_float(payload.get("fair_yes_prob"))
        probability_gap = abs(fair_yes_prob - market_yes_prob)
        side_counter[side] = side_counter.get(side, 0) + 1
        confidence_sum += confidence
        edge_sum_bps += edge_bps
        probability_gap_sum += probability_gap
        rows.append(
            PredictionRowResponse(
                market_id=to_text(payload.get("market_id")) or "n/a",
                selected_side=side,
                market_yes_prob=market_yes_prob,
                fair_yes_prob=fair_yes_prob,
                edge_bps=round(edge_bps, 2),
                confidence=confidence,
                rationale=to_text_tuple(payload.get("rationale")),
            )
        )
    rows.sort(key=lambda item: abs(item.edge_bps), reverse=True)
    count = len(rows)
    avg_confidence = round(confidence_sum / count, 4) if count > 0 else 0.0
    avg_edge_bps = round(edge_sum_bps / count, 2) if count > 0 else 0.0
    avg_probability_gap = round(probability_gap_sum / count, 4) if count > 0 else 0.0
    panel_status = "ok"
    if drift_alert is not None and drift_alert.overall_status == "critical":
        panel_status = "critical"
    elif (
        (drift_alert is not None and drift_alert.overall_status == "warning")
        or parity_warning_count > 0
        or (shadow_summary is not None and shadow_summary.rows_with_parity_warnings > 0)
    ):
        panel_status = "warning"
    return PredictionTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=count > 0,
        note="prediction_data_loaded" if count > 0 else "prediction_data_missing",
        panel_status=panel_status,
        predictions_count=count,
        avg_confidence=avg_confidence,
        avg_edge_bps=avg_edge_bps,
        avg_probability_gap=avg_probability_gap,
        parity_warning_count=parity_warning_count,
        model_visibility=model_visibility,
        calibration_summary=calibration_summary,
        shadow_comparison=shadow_summary,
        shadow_history=shadow_history,
        approval_rate_summary=approval_rate_summary,
        disagreement_buckets=disagreement_buckets,
        enrichment_coverage=enrichment_coverage,
        disagreement_vs_baseline=disagreement_vs_baseline,
        drift_alert=drift_alert,
        diagnostics_summary=(
            ChartPointResponse(label="avg_probability_gap", value=float(avg_probability_gap)),
            ChartPointResponse(label="avg_edge_bps", value=float(avg_edge_bps)),
            ChartPointResponse(label="avg_confidence", value=float(avg_confidence)),
            ChartPointResponse(label="parity_warning_count", value=float(parity_warning_count)),
            ChartPointResponse(
                label="shadow_rows_with_parity_warnings",
                value=float(shadow_summary.rows_with_parity_warnings if shadow_summary is not None else 0),
            ),
        ),
        anomalies=tuple(anomalies),
        side_distribution=chart_from_counter(side_counter),
        rows=tuple(rows[:30]),
    )


def read_json_mapping(path: Path) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, Mapping) else {}
