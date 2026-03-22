from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from prediction_market_bot.app.bootstrap import build_operational_repositories, build_persistence
from prediction_market_bot.app.config import load_settings
from prediction_market_bot.app.logging import configure_logging
from prediction_market_bot.cli.common import control_state_path, require_cli_role
from prediction_market_bot.services.model_promotion import (
    build_model_drift_report,
    evaluate_model_promotion,
    read_model_artifact_version,
    resolve_runtime_model_gate_decision,
)
from prediction_market_bot.services.operator_control import load_operator_state, save_operator_state

logger = logging.getLogger(__name__)


def evaluate_model_promotion_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    dataset_id: str | None = None,
    benchmark_run_id: str | None = None,
    training_run_id: str | None = None,
    walk_forward_run_id: str | None = None,
    shadow_run_id: str | None = None,
    output_path: Path | None = None,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    evaluation = evaluate_model_promotion(
        settings=settings,
        persistence=persistence,
        dataset_id=dataset_id,
        benchmark_run_id=benchmark_run_id,
        training_run_id=training_run_id,
        walk_forward_run_id=walk_forward_run_id,
        shadow_run_id=shadow_run_id,
    )
    payload = evaluation.to_dict()
    run_ref = payload.get("shadow_run_id") or payload.get("walk_forward_run_id") or "operator"
    persistence.write_artifact(str(run_ref), "model_promotion_evaluations", payload)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model promotion evaluation "
            f"passed={evaluation.passed} dataset_id={evaluation.dataset_id} model_version={evaluation.model_version or 'unset'}"
        )
        for check in evaluation.checks:
            state = "PASS" if check.passed else "FAIL"
            print(f"[{state}] {check.name}: {check.detail}")
        for warning in evaluation.warnings:
            print(f"[WARN] {warning}")
    return 0 if evaluation.passed else 2


def model_promotion_status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None = None,
    reference_runs: int | None = None,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(control_state_path(settings), repository=operational.operator_control_state)
    gate = resolve_runtime_model_gate_decision(settings=settings, state=state)

    drift_payload: dict[str, object] = {}
    try:
        drift_payload = build_model_drift_report(
            settings=settings,
            persistence=persistence,
            run_id=run_id,
            reference_runs=reference_runs,
        ).to_dict()
    except Exception as exc:
        if run_id:
            drift_payload = {
                "current_run_id": run_id,
                "overall_status": "error",
                "warnings": [f"drift_report_failed: {exc}"],
            }

    payload = {
        "promotion_enabled": settings.model_promotion.enabled,
        "gate_required_for_mode": gate.gate_required,
        "runtime_mode": settings.runtime.mode.value,
        "gate_decision": gate.to_dict(),
        "model_v2_promoted": state.model_v2_promoted,
        "model_v2_promoted_model_version": state.model_v2_promoted_model_version,
        "model_v2_promoted_at": state.model_v2_promoted_at,
        "model_v2_promotion_rationale": state.model_v2_promotion_rationale,
        "model_v2_rollback_active": state.model_v2_rollback_active,
        "model_v2_rollback_reason": state.model_v2_rollback_reason,
        "model_v2_rollback_at": state.model_v2_rollback_at,
        "model_v2_last_drift_status": state.model_v2_last_drift_status,
        "model_v2_last_drift_checked_at": state.model_v2_last_drift_checked_at,
        "drift_report": drift_payload,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model promotion status "
            f"requested={gate.requested_engine or 'unset'} effective={gate.effective_engine or 'unset'} "
            f"reason={gate.reason}"
        )
        print(
            "Promotion state "
            f"promoted={state.model_v2_promoted} version={state.model_v2_promoted_model_version or 'unset'} "
            f"rollback_active={state.model_v2_rollback_active}"
        )
        if drift_payload:
            print(
                "Drift status "
                f"overall={str(drift_payload.get('overall_status') or 'unknown')} "
                f"run_id={str(drift_payload.get('current_run_id') or 'unset')}"
            )
    return 0


def promote_model_v2_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    rationale: str,
    model_version: str = "",
    dataset_id: str | None = None,
    benchmark_run_id: str | None = None,
    training_run_id: str | None = None,
    walk_forward_run_id: str | None = None,
    shadow_run_id: str | None = None,
    force: bool = False,
    as_json: bool = False,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="promote-model-v2",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"promote-model-v2 blocked: {exc}")
        return 2

    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)

    evaluation_payload: dict[str, object] | None = None
    evaluation = evaluate_model_promotion(
        settings=settings,
        persistence=persistence,
        dataset_id=dataset_id,
        benchmark_run_id=benchmark_run_id,
        training_run_id=training_run_id,
        walk_forward_run_id=walk_forward_run_id,
        shadow_run_id=shadow_run_id,
    )
    evaluation_payload = evaluation.to_dict()
    if not evaluation.passed and not force:
        print("Promotion blocked: model promotion evaluation failed. Use --force only with explicit sign-off.")
        print(json.dumps(evaluation_payload, indent=2))
        return 2

    promoted_version = model_version.strip() or read_model_artifact_version(settings.prediction.model_artifact_path)
    if not promoted_version:
        print("Promotion blocked: model_version is empty (provide --model-version or valid model artifact).")
        return 1

    now_iso = datetime.now(UTC).isoformat()
    state.model_v2_promoted = True
    state.model_v2_promoted_model_version = promoted_version
    state.model_v2_promoted_at = now_iso
    state.model_v2_promotion_rationale = rationale.strip()
    state.model_v2_promotion_evaluation_run_id = str(evaluation_payload.get("shadow_run_id") or "")
    state.model_v2_rollback_active = False
    state.model_v2_rollback_reason = ""
    state.model_v2_rollback_at = ""
    save_operator_state(state_path, state, repository=operational.operator_control_state)

    event_payload = {
        "model_version": promoted_version,
        "rationale": state.model_v2_promotion_rationale,
        "force": bool(force),
        "acting_user": actor_user,
        "acting_role": actor_role,
        "evaluation": evaluation_payload,
    }
    persistence.write_run_event("operator", "model_v2_promoted", event_payload)
    persistence.write_artifact("operator", "model_promotion_decisions", {"decision": "promote", **event_payload})

    response = {
        "status": "promoted",
        "model_version": promoted_version,
        "promoted_at": now_iso,
        "force": bool(force),
        "evaluation_passed": evaluation.passed,
    }
    if as_json:
        print(json.dumps(response, indent=2))
    else:
        print(
            "Model v2 promoted "
            f"model_version={promoted_version} force={bool(force)} evaluation_passed={evaluation.passed}"
        )
    logger.warning(
        "model_v2_promoted",
        extra={
            "event": "model_v2_promoted",
            "model_version": promoted_version,
            "force": bool(force),
            "acting_user": actor_user,
            "acting_role": actor_role,
        },
    )
    return 0


def rollback_model_v2_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    rationale: str,
    as_json: bool = False,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="rollback-model-v2",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"rollback-model-v2 blocked: {exc}")
        return 2

    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    now_iso = datetime.now(UTC).isoformat()
    state.model_v2_rollback_active = True
    state.model_v2_rollback_reason = rationale.strip()
    state.model_v2_rollback_at = now_iso
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    payload = {
        "rollback_active": True,
        "rollback_reason": state.model_v2_rollback_reason,
        "rollback_at": now_iso,
        "acting_user": actor_user,
        "acting_role": actor_role,
    }
    persistence.write_run_event("operator", "model_v2_rollback_enabled", payload)
    persistence.write_artifact("operator", "model_promotion_decisions", {"decision": "rollback_enable", **payload})
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Model v2 rollback enabled reason='{state.model_v2_rollback_reason or 'not_set'}'")
    return 0


def clear_model_v2_rollback_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    rationale: str = "",
    as_json: bool = False,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="clear-model-v2-rollback",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"clear-model-v2-rollback blocked: {exc}")
        return 2

    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    now_iso = datetime.now(UTC).isoformat()
    state.model_v2_rollback_active = False
    state.model_v2_rollback_reason = ""
    state.model_v2_rollback_at = ""
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    payload = {
        "rollback_active": False,
        "cleared_at": now_iso,
        "rationale": rationale.strip(),
        "acting_user": actor_user,
        "acting_role": actor_role,
    }
    persistence.write_run_event("operator", "model_v2_rollback_cleared", payload)
    persistence.write_artifact("operator", "model_promotion_decisions", {"decision": "rollback_clear", **payload})
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print("Model v2 rollback cleared.")
    return 0


def drift_status_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    run_id: str | None = None,
    reference_runs: int | None = None,
    as_json: bool = False,
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    persistence = build_persistence(settings)
    operational = build_operational_repositories(settings)
    state_path = control_state_path(settings)
    state = load_operator_state(state_path, repository=operational.operator_control_state)
    report = build_model_drift_report(
        settings=settings,
        persistence=persistence,
        run_id=run_id,
        reference_runs=reference_runs,
    )
    payload = report.to_dict()
    if report.current_run_id:
        persistence.write_artifact(report.current_run_id, "model_drift_reports", payload)
    state.model_v2_last_drift_status = report.overall_status
    state.model_v2_last_drift_checked_at = report.created_at_utc
    save_operator_state(state_path, state, repository=operational.operator_control_state)
    if report.overall_status in {"warning", "critical"}:
        persistence.write_run_event(
            "operator",
            "model_drift_warning",
            {
                "run_id": report.current_run_id,
                "overall_status": report.overall_status,
                "signals": [signal.to_dict() for signal in report.signals],
            },
        )
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Model drift report "
            f"run_id={report.current_run_id or 'unset'} overall_status={report.overall_status} "
            f"signals={len(report.signals)}"
        )
        for signal in report.signals:
            print(f"[{signal.status.upper()}] {signal.name}: {signal.detail}")
    return 0 if report.overall_status in {"ok", "insufficient_data"} else 2


def record_threshold_tuning_command(
    config_path: Path,
    agents_config_path: Path,
    *,
    rationale: str,
    dataset_id: str = "",
    target_model_version: str = "",
    min_confidence: float | None = None,
    min_edge_bps: int | None = None,
    approval_rate_min: float | None = None,
    approval_rate_max: float | None = None,
    ticket: str = "",
    as_json: bool = False,
    acting_user: str = "",
    acting_role: str = "",
) -> int:
    settings = load_settings(config_path, agents_config_path)
    configure_logging(settings.logging)
    try:
        actor_user, actor_role = require_cli_role(
            settings=settings,
            required_role="admin",
            action_name="record-threshold-tuning",
            acting_user=acting_user,
            acting_role=acting_role,
        )
    except PermissionError as exc:
        print(f"record-threshold-tuning blocked: {exc}")
        return 2

    persistence = build_persistence(settings)
    now_iso = datetime.now(UTC).isoformat()
    payload = {
        "recorded_at_utc": now_iso,
        "runtime_mode": settings.runtime.mode.value,
        "dataset_id": dataset_id.strip() or settings.strategy_research.default_dataset_id,
        "target_model_version": target_model_version.strip()
        or read_model_artifact_version(settings.prediction.model_artifact_path),
        "rationale": rationale.strip(),
        "ticket": ticket.strip(),
        "acting_user": actor_user,
        "acting_role": actor_role,
        "proposed_thresholds": {
            "min_confidence": settings.prediction.min_confidence if min_confidence is None else float(min_confidence),
            "min_edge_bps": settings.prediction.min_edge_bps if min_edge_bps is None else int(min_edge_bps),
            "approval_rate_min": (
                settings.model_promotion.approval_rate_min if approval_rate_min is None else float(approval_rate_min)
            ),
            "approval_rate_max": (
                settings.model_promotion.approval_rate_max if approval_rate_max is None else float(approval_rate_max)
            ),
        },
        "current_thresholds": {
            "min_confidence": settings.prediction.min_confidence,
            "min_edge_bps": settings.prediction.min_edge_bps,
            "approval_rate_min": settings.model_promotion.approval_rate_min,
            "approval_rate_max": settings.model_promotion.approval_rate_max,
        },
    }
    persistence.write_artifact("operator", "model_threshold_tuning_records", payload)
    persistence.write_run_event("operator", "model_threshold_tuning_recorded", payload)
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            "Threshold tuning record saved "
            f"target_model_version={payload['target_model_version'] or 'unset'} "
            f"ticket={payload['ticket'] or 'none'}"
        )
    return 0

