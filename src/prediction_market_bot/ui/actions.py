from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from prediction_market_bot.app.bootstrap import build_http_client
from prediction_market_bot.app.secrets import build_secret_provider
from prediction_market_bot.domain.enums import TradeReviewAction
from prediction_market_bot.infrastructure.sandbox_chain import SandboxChainExecutor
from prediction_market_bot.cli.commands.ops_commands import pause_command, resume_command
from prediction_market_bot.cli.commands.run_commands import run_once_command
from prediction_market_bot.services import SandboxTransactionService, TradeReviewQueueService, validate_startup
from prediction_market_bot.ui.models import OperatorActionResponse
from prediction_market_bot.ui.read_models import UiRuntimeContext


@dataclass(slots=True)
class UiOperatorActionService:
    context: UiRuntimeContext

    def run_once(
        self,
        *,
        run_id: str = "",
        force: bool = False,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        safe_run_id = run_id.strip()
        try:
            exit_code = run_once_command(
                config_path=Path(self.context.config_path),
                agents_config_path=Path(self.context.agents_config_path),
                run_id=safe_run_id or None,
                force=force,
            )
        except Exception:
            return self._audit(
                action="run-once",
                accepted=False,
                status="failed",
                message="run_once_request_failed_before_execution",
                run_id=safe_run_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if exit_code == 0:
            return self._audit(
                action="run-once",
                accepted=True,
                status="completed",
                message="run_completed",
                run_id=safe_run_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if exit_code == 2:
            return self._audit(
                action="run-once",
                accepted=False,
                status="blocked",
                message="run_blocked_by_operator_pause",
                run_id=safe_run_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action="run-once",
            accepted=False,
            status="failed",
            message="run_failed_check_runtime_logs",
            run_id=safe_run_id,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def pause(self, *, reason: str, acting_user: str, acting_role: str) -> OperatorActionResponse:
        safe_reason = reason.strip() or "ui_operator_pause"
        try:
            exit_code = pause_command(
                config_path=Path(self.context.config_path),
                agents_config_path=Path(self.context.agents_config_path),
                reason=safe_reason,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        except Exception:
            return self._audit(
                action="pause",
                accepted=False,
                status="failed",
                message="pause_request_failed",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if exit_code == 0:
            return self._audit(
                action="pause",
                accepted=True,
                status="completed",
                message="operator_pause_enabled",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action="pause",
            accepted=False,
            status="failed",
            message="operator_pause_not_applied",
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def resume(self, *, acting_user: str, acting_role: str) -> OperatorActionResponse:
        try:
            exit_code = resume_command(
                config_path=Path(self.context.config_path),
                agents_config_path=Path(self.context.agents_config_path),
                acting_user=acting_user,
                acting_role=acting_role,
            )
        except Exception:
            return self._audit(
                action="resume",
                accepted=False,
                status="failed",
                message="resume_request_failed",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if exit_code == 0:
            return self._audit(
                action="resume",
                accepted=True,
                status="completed",
                message="operator_pause_cleared",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action="resume",
            accepted=False,
            status="failed",
            message="operator_resume_not_applied",
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def admin_settings(
        self,
        *,
        setting: str,
        value: str,
        confirmed: bool,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        safe_setting = setting.strip().lower()
        safe_value = value.strip()
        if not confirmed:
            return self._audit(
                action="admin-settings",
                accepted=False,
                status="blocked",
                message="confirmation_required",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if not safe_setting:
            return self._audit(
                action="admin-settings",
                accepted=False,
                status="failed",
                message="admin_setting_key_missing",
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action="admin-settings",
            accepted=True,
            status="completed",
            message=f"admin_setting_recorded key={safe_setting} value={safe_value}",
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def review_approve(
        self,
        *,
        queue_id: str,
        operator_id: str,
        rationale: str,
        note: str,
        confirmed: bool,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        return self._review_action(
            queue_id=queue_id,
            operator_id=operator_id,
            rationale=rationale,
            note=note,
            confirmed=confirmed,
            action=TradeReviewAction.APPROVE,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def review_reject(
        self,
        *,
        queue_id: str,
        operator_id: str,
        rationale: str,
        note: str,
        confirmed: bool,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        return self._review_action(
            queue_id=queue_id,
            operator_id=operator_id,
            rationale=rationale,
            note=note,
            confirmed=confirmed,
            action=TradeReviewAction.REJECT,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def tx_reconcile(
        self,
        *,
        run_id: str,
        intent_id: str,
        limit: int,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        startup_error = self._validate_startup()
        if startup_error:
            return self._audit(
                action="tx-reconcile",
                accepted=False,
                status="failed",
                message=startup_error,
                run_id=run_id,
                intent_id=intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        service = self._build_sandbox_tx_service()
        try:
            rows = service.reconcile(
                run_id=run_id.strip() or None,
                intent_id=intent_id.strip() or None,
                limit=max(limit, 0),
            )
        except Exception:
            return self._audit(
                action="tx-reconcile",
                accepted=False,
                status="failed",
                message="tx_reconcile_failed",
                run_id=run_id,
                intent_id=intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        message = "tx_reconcile_completed" if rows else "tx_reconcile_no_matching_intents"
        target_run = run_id.strip() or (rows[0].run_id if rows else "")
        target_intent = intent_id.strip() or (rows[0].intent_id if rows else "")
        return self._audit(
            action="tx-reconcile",
            accepted=True,
            status="completed",
            message=f"{message} reconciled_count={len(rows)}",
            run_id=target_run,
            intent_id=target_intent,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def tx_resubmit_safe(
        self,
        *,
        intent_id: str,
        confirmed: bool,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        safe_intent_id = intent_id.strip()
        if not confirmed:
            return self._audit(
                action="tx-resubmit-safe",
                accepted=False,
                status="blocked",
                message="confirmation_required",
                intent_id=safe_intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        startup_error = self._validate_startup()
        if startup_error:
            return self._audit(
                action="tx-resubmit-safe",
                accepted=False,
                status="failed",
                message=startup_error,
                intent_id=safe_intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        if not self.context.settings.sandbox_chain.submit_tx:
            return self._audit(
                action="tx-resubmit-safe",
                accepted=False,
                status="blocked",
                message="tx_resubmit_safe_requires_submit_tx_true",
                intent_id=safe_intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        service = self._build_sandbox_tx_service()
        try:
            row = service.resubmit_safe(intent_id=safe_intent_id)
        except KeyError:
            return self._audit(
                action="tx-resubmit-safe",
                accepted=False,
                status="failed",
                message="tx_intent_not_found",
                intent_id=safe_intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        except ValueError as exc:
            return self._audit(
                action="tx-resubmit-safe",
                accepted=False,
                status="blocked",
                message=f"tx_resubmit_safe_blocked {exc}",
                run_id="",
                intent_id=safe_intent_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action="tx-resubmit-safe",
            accepted=True,
            status="completed",
            message="tx_resubmit_safe_completed",
            run_id=row.run_id,
            intent_id=row.intent_id,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def _review_action(
        self,
        *,
        queue_id: str,
        operator_id: str,
        rationale: str,
        note: str,
        confirmed: bool,
        action: TradeReviewAction,
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        safe_queue_id = queue_id.strip()
        safe_operator_id = operator_id.strip() or acting_user.strip()
        safe_rationale = rationale.strip()
        safe_note = note.strip()
        action_label = "review-approve" if action == TradeReviewAction.APPROVE else "review-reject"

        if not confirmed:
            return self._audit(
                action=action_label,
                accepted=False,
                status="blocked",
                message="confirmation_required",
                queue_id=safe_queue_id,
                operator_id=safe_operator_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )

        queue = TradeReviewQueueService(
            self.context.persistence,
            candidate_repo=self.context.operational.review_queue,
            decision_repo=self.context.operational.review_decisions,
        )
        try:
            item = queue.apply_action(
                queue_id=safe_queue_id,
                action=action,
                operator_id=safe_operator_id,
                operator_rationale=safe_rationale,
                note=safe_note,
            )
        except KeyError:
            return self._audit(
                action=action_label,
                accepted=False,
                status="failed",
                message="review_queue_item_not_found",
                queue_id=safe_queue_id,
                operator_id=safe_operator_id,
                acting_user=acting_user,
                acting_role=acting_role,
            )
        return self._audit(
            action=action_label,
            accepted=True,
            status="completed",
            message=f"review_item_{item.status.value.lower()}",
            run_id=item.run_id,
            queue_id=item.queue_id,
            operator_id=safe_operator_id,
            acting_user=acting_user,
            acting_role=acting_role,
        )

    def _validate_startup(self) -> str:
        report = validate_startup(
            settings=self.context.settings,
            persistence=self.context.persistence,
            operational=self.context.operational,
        )
        if report.ok:
            return ""
        failing = [check.name for check in report.checks if not check.ok and check.severity == "error"]
        if failing:
            return f"startup_validation_failed {';'.join(failing)}"
        return "startup_validation_failed"

    def _build_sandbox_tx_service(self) -> SandboxTransactionService:
        settings = self.context.settings
        secrets = build_secret_provider(settings)
        http_client = build_http_client(settings, secrets=secrets)
        private_key = ""
        private_key_env = settings.sandbox_chain.private_key_env.strip()
        if private_key_env:
            private_key = secrets.get(private_key_env)

        executor = SandboxChainExecutor(
            rpc_url=settings.sandbox_chain.rpc_url,
            contract_address=settings.sandbox_chain.contract_address,
            chain_id=settings.sandbox_chain.chain_id,
            from_address=settings.sandbox_chain.from_address,
            intent_method_selector=settings.sandbox_chain.intent_method_selector,
            submit_tx=settings.sandbox_chain.submit_tx,
            private_key=private_key,
            allow_unlocked_send=settings.sandbox_chain.allow_unlocked_send,
            gas_limit=settings.sandbox_chain.gas_limit,
            confirmations_required=settings.sandbox_chain.confirmations_required,
            dropped_after_sec=settings.sandbox_chain.dropped_after_sec,
            timeout_sec=settings.sandbox_chain.request_timeout_sec,
            max_retries=settings.http.max_retries,
            retry_backoff_sec=settings.http.retry_backoff_sec,
            retry_jitter_sec=settings.http.retry_jitter_sec,
            enabled=settings.sandbox_chain.enabled,
            http_client=http_client,
        )
        return SandboxTransactionService(
            executor=executor,
            intent_repo=self.context.operational.transaction_intents,
            attempt_repo=self.context.operational.transaction_attempts,
            receipt_repo=self.context.operational.transaction_receipts,
        )

    def _audit(
        self,
        *,
        action: str,
        accepted: bool,
        status: str,
        message: str,
        run_id: str = "",
        queue_id: str = "",
        intent_id: str = "",
        operator_id: str = "",
        acting_user: str,
        acting_role: str,
    ) -> OperatorActionResponse:
        now = datetime.now(UTC).isoformat()
        action_id = f"ui-{uuid4().hex}"
        safe_run_id = run_id.strip() or "ui-control-plane"
        payload = {
            "action_id": action_id,
            "action": action,
            "accepted": accepted,
            "status": status,
            "message": message,
            "created_at": now,
            "run_id": run_id.strip(),
            "queue_id": queue_id.strip(),
            "intent_id": intent_id.strip(),
            "operator_id": operator_id.strip(),
            "acting_user": acting_user.strip(),
            "acting_role": acting_role.strip(),
        }
        self.context.persistence.write_artifact(safe_run_id, "ui_operator_actions", payload)
        self.context.persistence.write_run_event(
            safe_run_id,
            "ui_operator_action",
            {
                "action_id": action_id,
                "action": action,
                "accepted": accepted,
                "status": status,
                "run_id": run_id.strip(),
                "queue_id": queue_id.strip(),
                "intent_id": intent_id.strip(),
                "acting_user": acting_user.strip(),
                "acting_role": acting_role.strip(),
            },
        )
        return OperatorActionResponse(
            action=action,
            accepted=accepted,
            status=status,
            message=message,
            run_id=run_id.strip(),
            queue_id=queue_id.strip(),
            intent_id=intent_id.strip(),
            operator_id=operator_id.strip(),
            acting_user=acting_user.strip(),
            acting_role=acting_role.strip(),
            audit_action_id=action_id,
        )
