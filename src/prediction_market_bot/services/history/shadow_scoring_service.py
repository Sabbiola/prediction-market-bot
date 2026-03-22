from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Mapping

from prediction_market_bot.infrastructure.persistence import JsonlPersistence


def _as_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    return False


def _clamp_probability(value: float) -> float:
    return min(max(value, 1e-6), 1.0 - 1e-6)


@dataclass(slots=True, frozen=True)
class ShadowScoringReport:
    run_id: str
    total_rows: int
    rows_with_model_v2: int
    rows_with_alt_llm_shadow: int
    rows_with_parity_warnings: int
    calibration: Mapping[str, float | int | None]
    calibration_alt_llm: Mapping[str, float | int | None]
    edge: Mapping[str, float | int | None]
    edge_alt_llm: Mapping[str, float | int | None]
    prediction_delta: Mapping[str, float | int | None]
    confidence_delta: Mapping[str, float | int | None]
    approval_rate: Mapping[str, float | int | None]
    approval_rate_alt_llm: Mapping[str, float | int | None]
    disagreement_buckets: Mapping[str, int]
    alt_llm_disagreement_buckets: Mapping[str, int]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "total_rows": self.total_rows,
            "rows_with_model_v2": self.rows_with_model_v2,
            "rows_with_alt_llm_shadow": self.rows_with_alt_llm_shadow,
            "rows_with_parity_warnings": self.rows_with_parity_warnings,
            "calibration": dict(self.calibration),
            "calibration_alt_llm": dict(self.calibration_alt_llm),
            "edge": dict(self.edge),
            "edge_alt_llm": dict(self.edge_alt_llm),
            "prediction_delta": dict(self.prediction_delta),
            "confidence_delta": dict(self.confidence_delta),
            "approval_rate": dict(self.approval_rate),
            "approval_rate_alt_llm": dict(self.approval_rate_alt_llm),
            "disagreement_buckets": dict(self.disagreement_buckets),
            "alt_llm_disagreement_buckets": dict(self.alt_llm_disagreement_buckets),
            "warnings": list(self.warnings),
        }


def build_shadow_scoring_report(persistence: JsonlPersistence, run_id: str) -> ShadowScoringReport:
    shadow_rows = persistence.read_artifact_records(run_id, "prediction_shadow_comparisons")
    warnings: list[str] = []
    if not shadow_rows:
        warnings.append("no_prediction_shadow_comparisons_for_run")
    payload_rows = [
        row.get("payload")
        for row in shadow_rows
        if isinstance(row, Mapping) and isinstance(row.get("payload"), Mapping)
    ]
    total_rows = len(payload_rows)
    rows_with_parity_warnings = 0
    rows_with_model_v2 = 0
    rows_with_alt_llm_shadow = 0
    alt_shadow_requested = False
    disagreement_buckets: dict[str, int] = {}
    alt_llm_disagreement_buckets: dict[str, int] = {}
    heuristic_edges: list[float] = []
    model_edges: list[float] = []
    alt_shadow_edges: list[float] = []
    edge_deltas: list[float] = []
    alt_shadow_edge_deltas: list[float] = []
    prediction_deltas: list[float] = []
    alt_shadow_prediction_deltas: list[float] = []
    confidence_deltas: list[float] = []
    alt_shadow_confidence_deltas: list[float] = []
    heuristic_approved_total = 0
    model_approved_total = 0
    alt_shadow_approved_total = 0
    approval_sample = 0

    settled_rows = persistence.read_artifact_records(run_id, "settlement_results")
    resolved_by_market: dict[str, int] = {}
    for row in settled_rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        market_id = str(payload.get("market_id") or "").strip()
        if not market_id:
            continue
        resolved_yes = payload.get("resolved_yes")
        if isinstance(resolved_yes, bool):
            resolved_by_market[market_id] = 1 if resolved_yes else 0

    heuristic_probs: list[float] = []
    model_probs: list[float] = []
    alt_shadow_probs: list[float] = []
    outcomes: list[int] = []
    alt_shadow_outcomes: list[int] = []

    for payload in payload_rows:
        assert isinstance(payload, Mapping)
        parity = payload.get("parity_warnings")
        if isinstance(parity, list) and parity:
            rows_with_parity_warnings += 1

        bucket = str(payload.get("disagreement_bucket") or "unknown").strip() or "unknown"
        disagreement_buckets[bucket] = disagreement_buckets.get(bucket, 0) + 1
        alt_bucket = str(payload.get("alt_llm_disagreement_bucket") or "").strip().lower()
        if alt_bucket and alt_bucket != "not_requested":
            alt_llm_disagreement_buckets[alt_bucket] = alt_llm_disagreement_buckets.get(alt_bucket, 0) + 1

        heuristic = payload.get("heuristic_prediction")
        model_v2 = payload.get("model_v2_prediction")
        alt_shadow = payload.get("alt_llm_shadow_prediction")
        alt_status = str(payload.get("alt_llm_shadow_status") or "").strip().lower()
        if alt_status and alt_status != "not_requested":
            alt_shadow_requested = True
        approvals = payload.get("approvals")
        if isinstance(heuristic, Mapping):
            heuristic_edges.append(_as_float(heuristic.get("edge")))
        if isinstance(model_v2, Mapping):
            rows_with_model_v2 += 1
            model_edges.append(_as_float(model_v2.get("edge")))
            if isinstance(heuristic, Mapping):
                edge_deltas.append(_as_float(model_v2.get("edge")) - _as_float(heuristic.get("edge")))
                prediction_deltas.append(
                    _as_float(model_v2.get("fair_yes_prob"), default=0.5)
                    - _as_float(heuristic.get("fair_yes_prob"), default=0.5)
                )
                confidence_deltas.append(
                    _as_float(model_v2.get("confidence"), default=0.0)
                    - _as_float(heuristic.get("confidence"), default=0.0)
                )

            market_id = str(payload.get("market_id") or "").strip()
            outcome = resolved_by_market.get(market_id)
            if outcome is not None and isinstance(heuristic, Mapping):
                heuristic_probs.append(_clamp_probability(_as_float(heuristic.get("fair_yes_prob"), default=0.5)))
                model_probs.append(_clamp_probability(_as_float(model_v2.get("fair_yes_prob"), default=0.5)))
                outcomes.append(outcome)
        if isinstance(alt_shadow, Mapping):
            rows_with_alt_llm_shadow += 1
            alt_shadow_edges.append(_as_float(alt_shadow.get("edge")))
            if isinstance(heuristic, Mapping):
                alt_shadow_edge_deltas.append(_as_float(alt_shadow.get("edge")) - _as_float(heuristic.get("edge")))
                alt_shadow_prediction_deltas.append(
                    _as_float(alt_shadow.get("fair_yes_prob"), default=0.5)
                    - _as_float(heuristic.get("fair_yes_prob"), default=0.5)
                )
                alt_shadow_confidence_deltas.append(
                    _as_float(alt_shadow.get("confidence"), default=0.0)
                    - _as_float(heuristic.get("confidence"), default=0.0)
                )

            market_id = str(payload.get("market_id") or "").strip()
            outcome = resolved_by_market.get(market_id)
            if outcome is not None and isinstance(heuristic, Mapping):
                alt_shadow_probs.append(_clamp_probability(_as_float(alt_shadow.get("fair_yes_prob"), default=0.5)))
                alt_shadow_outcomes.append(outcome)

        if isinstance(approvals, Mapping):
            heuristic_approved = approvals.get("heuristic")
            model_approved = approvals.get("model_v2")
            alt_shadow_approved = approvals.get("alt_llm_shadow")
            if isinstance(heuristic_approved, bool):
                approval_sample += 1
                if heuristic_approved:
                    heuristic_approved_total += 1
                if isinstance(model_approved, bool) and model_approved:
                    model_approved_total += 1
                if isinstance(alt_shadow_approved, bool) and alt_shadow_approved:
                    alt_shadow_approved_total += 1
            else:
                warnings.append("shadow_approval_payload_missing_heuristic_boolean")

    if total_rows > 0 and rows_with_model_v2 == 0:
        warnings.append("no_model_v2_predictions_available_in_shadow_rows")
    if alt_shadow_requested and rows_with_alt_llm_shadow == 0:
        warnings.append("no_alt_llm_shadow_predictions_available_in_shadow_rows")

    calibration_sample = len(outcomes)
    calibration: dict[str, float | int | None]
    if calibration_sample == 0:
        warnings.append("no_settlement_outcomes_for_calibration_delta")
        calibration = {
            "sample_size": 0,
            "heuristic_brier": None,
            "model_v2_brier": None,
            "brier_delta_model_minus_heuristic": None,
            "heuristic_calibration_gap": None,
            "model_v2_calibration_gap": None,
            "calibration_gap_delta_model_minus_heuristic": None,
        }
    else:
        heuristic_brier = sum((prob - float(outcome)) ** 2 for prob, outcome in zip(heuristic_probs, outcomes, strict=False)) / calibration_sample
        model_brier = sum((prob - float(outcome)) ** 2 for prob, outcome in zip(model_probs, outcomes, strict=False)) / calibration_sample
        observed_rate = sum(outcomes) / calibration_sample
        heuristic_gap = abs((sum(heuristic_probs) / calibration_sample) - observed_rate)
        model_gap = abs((sum(model_probs) / calibration_sample) - observed_rate)
        calibration = {
            "sample_size": calibration_sample,
            "heuristic_brier": round(heuristic_brier, 8),
            "model_v2_brier": round(model_brier, 8),
            "brier_delta_model_minus_heuristic": round(model_brier - heuristic_brier, 8),
            "heuristic_calibration_gap": round(heuristic_gap, 8),
            "model_v2_calibration_gap": round(model_gap, 8),
            "calibration_gap_delta_model_minus_heuristic": round(model_gap - heuristic_gap, 8),
        }
    alt_calibration_sample = len(alt_shadow_outcomes)
    calibration_alt_llm: dict[str, float | int | None]
    if alt_calibration_sample == 0:
        if alt_shadow_requested:
            warnings.append("no_settlement_outcomes_for_alt_llm_calibration_delta")
        calibration_alt_llm = {
            "sample_size": 0,
            "heuristic_brier": None,
            "alt_llm_brier": None,
            "brier_delta_alt_llm_minus_heuristic": None,
            "heuristic_calibration_gap": None,
            "alt_llm_calibration_gap": None,
            "calibration_gap_delta_alt_llm_minus_heuristic": None,
        }
    else:
        alt_brier = (
            sum((prob - float(outcome)) ** 2 for prob, outcome in zip(alt_shadow_probs, alt_shadow_outcomes, strict=False))
            / alt_calibration_sample
        )
        observed_rate = sum(alt_shadow_outcomes) / alt_calibration_sample
        heuristic_sample_probs = heuristic_probs[:alt_calibration_sample]
        heuristic_brier_alt_sample = (
            sum((prob - float(outcome)) ** 2 for prob, outcome in zip(heuristic_sample_probs, alt_shadow_outcomes, strict=False))
            / alt_calibration_sample
        )
        heuristic_gap = abs((sum(heuristic_sample_probs) / alt_calibration_sample) - observed_rate)
        alt_gap = abs((sum(alt_shadow_probs) / alt_calibration_sample) - observed_rate)
        calibration_alt_llm = {
            "sample_size": alt_calibration_sample,
            "heuristic_brier": round(heuristic_brier_alt_sample, 8),
            "alt_llm_brier": round(alt_brier, 8),
            "brier_delta_alt_llm_minus_heuristic": round(alt_brier - heuristic_brier_alt_sample, 8),
            "heuristic_calibration_gap": round(heuristic_gap, 8),
            "alt_llm_calibration_gap": round(alt_gap, 8),
            "calibration_gap_delta_alt_llm_minus_heuristic": round(alt_gap - heuristic_gap, 8),
        }

    edge = {
        "sample_size": rows_with_model_v2,
        "mean_heuristic_edge": round(mean(heuristic_edges), 8) if heuristic_edges else None,
        "mean_model_v2_edge": round(mean(model_edges), 8) if model_edges else None,
        "mean_edge_delta_model_minus_heuristic": round(mean(edge_deltas), 8) if edge_deltas else None,
    }
    edge_alt_llm = {
        "sample_size": rows_with_alt_llm_shadow,
        "mean_heuristic_edge": round(mean(heuristic_edges), 8) if heuristic_edges else None,
        "mean_alt_llm_edge": round(mean(alt_shadow_edges), 8) if alt_shadow_edges else None,
        "mean_edge_delta_alt_llm_minus_heuristic": (
            round(mean(alt_shadow_edge_deltas), 8) if alt_shadow_edge_deltas else None
        ),
    }
    prediction_delta = {
        "sample_size_model_v2": rows_with_model_v2,
        "mean_model_v2_minus_heuristic": round(mean(prediction_deltas), 8) if prediction_deltas else None,
        "sample_size_alt_llm": rows_with_alt_llm_shadow,
        "mean_alt_llm_minus_heuristic": (
            round(mean(alt_shadow_prediction_deltas), 8) if alt_shadow_prediction_deltas else None
        ),
    }
    confidence_delta = {
        "sample_size_model_v2": rows_with_model_v2,
        "mean_model_v2_minus_heuristic": round(mean(confidence_deltas), 8) if confidence_deltas else None,
        "sample_size_alt_llm": rows_with_alt_llm_shadow,
        "mean_alt_llm_minus_heuristic": (
            round(mean(alt_shadow_confidence_deltas), 8) if alt_shadow_confidence_deltas else None
        ),
    }
    approval_rate = {
        "sample_size": approval_sample,
        "heuristic_rate": round((heuristic_approved_total / approval_sample), 8) if approval_sample > 0 else None,
        "model_v2_rate": round((model_approved_total / approval_sample), 8) if approval_sample > 0 else None,
        "approval_rate_delta_model_minus_heuristic": (
            round((model_approved_total - heuristic_approved_total) / approval_sample, 8) if approval_sample > 0 else None
        ),
    }
    approval_rate_alt_llm = {
        "sample_size": approval_sample,
        "heuristic_rate": round((heuristic_approved_total / approval_sample), 8) if approval_sample > 0 else None,
        "alt_llm_rate": round((alt_shadow_approved_total / approval_sample), 8) if approval_sample > 0 else None,
        "approval_rate_delta_alt_llm_minus_heuristic": (
            round((alt_shadow_approved_total - heuristic_approved_total) / approval_sample, 8)
            if approval_sample > 0
            else None
        ),
    }
    return ShadowScoringReport(
        run_id=run_id,
        total_rows=total_rows,
        rows_with_model_v2=rows_with_model_v2,
        rows_with_alt_llm_shadow=rows_with_alt_llm_shadow,
        rows_with_parity_warnings=rows_with_parity_warnings,
        calibration=calibration,
        calibration_alt_llm=calibration_alt_llm,
        edge=edge,
        edge_alt_llm=edge_alt_llm,
        prediction_delta=prediction_delta,
        confidence_delta=confidence_delta,
        approval_rate=approval_rate,
        approval_rate_alt_llm=approval_rate_alt_llm,
        disagreement_buckets={key: disagreement_buckets[key] for key in sorted(disagreement_buckets)},
        alt_llm_disagreement_buckets={
            key: alt_llm_disagreement_buckets[key] for key in sorted(alt_llm_disagreement_buckets)
        },
        warnings=tuple(dict.fromkeys(warnings)),
    )


def render_shadow_scoring_report_markdown(report: ShadowScoringReport) -> str:
    lines = [
        f"# Shadow Scoring Report: {report.run_id}",
        "",
        "## Summary",
        f"- total_rows: {report.total_rows}",
        f"- rows_with_model_v2: {report.rows_with_model_v2}",
        f"- rows_with_alt_llm_shadow: {report.rows_with_alt_llm_shadow}",
        f"- rows_with_parity_warnings: {report.rows_with_parity_warnings}",
        "",
        "## Calibration Deltas",
    ]
    for key, value in report.calibration.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Calibration Deltas (Alt LLM Shadow)",
        ]
    )
    for key, value in report.calibration_alt_llm.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Edge Deltas",
        ]
    )
    for key, value in report.edge.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Edge Deltas (Alt LLM Shadow)",
        ]
    )
    for key, value in report.edge_alt_llm.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Prediction Deltas",
        ]
    )
    for key, value in report.prediction_delta.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Confidence Deltas",
        ]
    )
    for key, value in report.confidence_delta.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Approval Rate Deltas",
        ]
    )
    for key, value in report.approval_rate.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Approval Rate Deltas (Alt LLM Shadow)",
        ]
    )
    for key, value in report.approval_rate_alt_llm.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Disagreement Buckets",
        ]
    )
    if report.disagreement_buckets:
        for bucket, count in report.disagreement_buckets.items():
            lines.append(f"- {bucket}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend(
        [
            "",
            "## Disagreement Buckets (Alt LLM Shadow)",
        ]
    )
    if report.alt_llm_disagreement_buckets:
        for bucket, count in report.alt_llm_disagreement_buckets.items():
            lines.append(f"- {bucket}: {count}")
    else:
        lines.append("- none: 0")
    lines.extend(
        [
            "",
            "## Warnings",
        ]
    )
    if report.warnings:
        for warning in report.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"
