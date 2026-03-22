from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UiBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthSessionResponse(UiBaseModel):
    authenticated: bool
    username: str = ""
    role: str = ""
    session_expires_at: str = ""


class LoginRequest(UiBaseModel):
    username: str
    password: str


class LoginResponse(UiBaseModel):
    authenticated: bool
    status: str
    message: str
    user: AuthSessionResponse


class RuntimeMetricsResponse(UiBaseModel):
    live_source_failures_total: int
    review_queue_depth: int
    open_positions_count: int
    pending_settlements_count: int
    tx_pending_count: int
    tx_mined_count: int
    tx_failed_count: int
    stale_data_events_total: int
    stale_data_blocked_trades_total: int


class HealthResponse(UiBaseModel):
    status: str
    app_name: str
    environment: str
    runtime_mode: str
    execution_mode: str
    timestamp_utc: str


class ReadinessCheckResponse(UiBaseModel):
    name: str
    ok: bool
    severity: str
    detail: str


class ReadinessResponse(UiBaseModel):
    status: str
    ready: bool
    checks: tuple[ReadinessCheckResponse, ...]
    metrics: RuntimeMetricsResponse


class OperatorStateResponse(UiBaseModel):
    paused: bool
    pause_reason: str
    updated_at: str
    last_run_id: str
    last_run_status: str
    last_run_started_at: str
    last_run_finished_at: str
    last_error: str
    last_report_markdown_path: str
    last_report_json_path: str
    scheduler_last_started_at: str
    scheduler_last_tick_at: str
    scheduler_iterations: int


class RunOptionResponse(UiBaseModel):
    run_id: str
    label: str


class RunSelectorResponse(UiBaseModel):
    selected_run_id: str
    latest_run_id: str
    options: tuple[RunOptionResponse, ...]
    available: bool
    note: str


class ChartPointResponse(UiBaseModel):
    label: str
    value: float


class IncidentBannerResponse(UiBaseModel):
    level: str
    code: str
    title: str
    detail: str
    recommendation: str


class IncidentEventResponse(UiBaseModel):
    timestamp: str
    run_id: str
    event_type: str
    severity: str
    summary: str


class ReportShortcutResponse(UiBaseModel):
    label: str
    command: str
    description: str


class LastRunSummaryResponse(UiBaseModel):
    run_id: str
    status: str
    started_at: str
    finished_at: str
    total_markets: int
    candidates: int
    executed: int
    settled: int
    wins: int
    losses: int
    skipped: int
    event_count: int
    available: bool
    note: str


class ModelVisibilityResponse(UiBaseModel):
    runtime_mode: str
    requested_engine: str
    effective_engine: str
    gate_required: bool
    gate_reason: str
    active_model_version: str
    model_name: str
    feature_schema_version: str
    calibration_version: str
    calibration_method: str
    active_source_set: tuple[str, ...] = ()
    promoted_model_version: str
    promoted_at: str
    rollback_active: bool
    rollback_reason: str


class DriftSignalResponse(UiBaseModel):
    name: str
    status: str
    detail: str
    current_value: float | None = None
    reference_value: float | None = None
    threshold: float | None = None


class DriftAlertResponse(UiBaseModel):
    overall_status: str
    current_run_id: str
    created_at_utc: str
    warnings: tuple[str, ...]
    signals: tuple[DriftSignalResponse, ...]


class ShadowComparisonHistoryRowResponse(UiBaseModel):
    run_id: str
    total_rows: int
    rows_with_model_v2: int
    rows_with_parity_warnings: int
    approval_rate_heuristic: float | None
    approval_rate_model_v2: float | None
    approval_rate_delta_model_minus_heuristic: float | None
    disagreement_buckets: tuple[ChartPointResponse, ...]


class OverviewTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    runtime_mode: str
    execution_mode: str
    operator_paused: bool
    operator_pause_reason: str
    last_run: LastRunSummaryResponse
    review_queue_depth: int
    open_positions_count: int
    pending_settlements_count: int
    live_source_failures_total: int
    tx_pending_count: int
    tx_mined_count: int
    tx_failed_count: int
    stale_data_events_total: int
    stale_data_blocked_trades_total: int
    model_visibility: ModelVisibilityResponse
    drift_alert: DriftAlertResponse | None = None
    counters_chart: tuple[ChartPointResponse, ...]
    incident_banners: tuple[IncidentBannerResponse, ...] = ()
    incidents_feed: tuple[IncidentEventResponse, ...] = ()


class StartupValidationSummaryResponse(UiBaseModel):
    ok: bool
    error_count: int
    warning_count: int
    info_count: int
    checks: tuple[ReadinessCheckResponse, ...]


class HealthcheckSummaryResponse(UiBaseModel):
    status: str
    runtime_mode: str
    execution_mode: str
    timestamp_utc: str
    metrics: RuntimeMetricsResponse


class DbConnectivitySummaryResponse(UiBaseModel):
    status: str
    detail: str
    check_name: str


class ProviderStatusSummaryResponse(UiBaseModel):
    status: str
    market_data_provider: str
    research_provider: str
    provider_failure_policy: str
    live_market_data_enabled: bool
    live_research_enabled: bool
    sandbox_chain_enabled: bool
    sandbox_submit_tx: bool
    live_source_failures_total: int


class SystemHealthTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    startup_validation: StartupValidationSummaryResponse
    healthcheck: HealthcheckSummaryResponse
    db_connectivity: DbConnectivitySummaryResponse
    provider_status: ProviderStatusSummaryResponse


class ScannerCandidateRowResponse(UiBaseModel):
    market_id: str
    title: str
    category: str
    scan_score: float
    reasons: tuple[str, ...]
    liquidity_usd: float
    volume_24h_usd: float
    spread_bps: int
    hours_to_resolution: float


class ScannerTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    total_markets: int
    candidates_count: int
    avg_scan_score: float
    top_reasons: tuple[ChartPointResponse, ...]
    candidates: tuple[ScannerCandidateRowResponse, ...]


class ResearchPacketRowResponse(UiBaseModel):
    market_id: str
    findings_count: int
    evidence_strength: float
    disagreement_score: float
    weighted_sentiment: float
    source_types: tuple[str, ...]
    narrative_summary: str


class ResearchTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    packets_count: int
    findings_count: int
    avg_evidence_strength: float
    avg_disagreement_score: float
    source_type_distribution: tuple[ChartPointResponse, ...]
    packets: tuple[ResearchPacketRowResponse, ...]


class PredictionRowResponse(UiBaseModel):
    market_id: str
    selected_side: str
    market_yes_prob: float
    fair_yes_prob: float
    edge_bps: float
    confidence: float
    rationale: tuple[str, ...]


class PredictionTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    predictions_count: int
    avg_confidence: float
    avg_edge_bps: float
    model_visibility: ModelVisibilityResponse
    calibration_summary: tuple[ChartPointResponse, ...] = ()
    shadow_comparison: ShadowComparisonHistoryRowResponse | None = None
    shadow_history: tuple[ShadowComparisonHistoryRowResponse, ...] = ()
    approval_rate_summary: tuple[ChartPointResponse, ...] = ()
    disagreement_buckets: tuple[ChartPointResponse, ...] = ()
    enrichment_coverage: float | None = None
    disagreement_vs_baseline: tuple[ChartPointResponse, ...] = ()
    drift_alert: DriftAlertResponse | None = None
    side_distribution: tuple[ChartPointResponse, ...]
    rows: tuple[PredictionRowResponse, ...]


class RiskRowResponse(UiBaseModel):
    market_id: str
    approved: bool
    side: str
    stake_usd: float
    bankroll_fraction: float
    fractional_kelly: float
    reasoning: tuple[str, ...]


class RiskTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    decisions_count: int
    approved_count: int
    blocked_count: int
    avg_stake_usd: float
    reason_code_distribution: tuple[ChartPointResponse, ...]
    rows: tuple[RiskRowResponse, ...]


class ExecutionRowResponse(UiBaseModel):
    market_id: str
    status: str
    side: str
    stake_usd: float
    fill_price: float | None
    order_id: str
    execution_mode: str
    intent_rationale: str
    message: str


class ExecutionTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    intents_count: int
    executions_count: int
    status_distribution: tuple[ChartPointResponse, ...]
    rows: tuple[ExecutionRowResponse, ...]


class SettlementRowResponse(UiBaseModel):
    market_id: str
    outcome_classification: str
    resolved_yes: str
    pnl_usd: float
    execution_side: str
    resolution_reason: str


class SettlementTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    pending_requests_count: int
    resolution_checks_count: int
    settled_count: int
    outcome_distribution: tuple[ChartPointResponse, ...]
    rows: tuple[SettlementRowResponse, ...]


class SandboxTxRowResponse(UiBaseModel):
    intent_id: str
    market_id: str
    side: str
    execution_mode: str
    confirmation_status: str
    tx_hash: str
    nonce: int | None
    retry_count: int
    replacement_for_tx_hash: str
    replaced_by_tx_hash: str
    message: str


class SandboxTxTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    attempts_count: int
    receipts_count: int
    confirmation_distribution: tuple[ChartPointResponse, ...]
    rows: tuple[SandboxTxRowResponse, ...]
    active_intent_id: str = ""
    can_reconcile: bool = False
    can_resubmit_safe: bool = False
    action_hint: str = ""
    selected_intent: SandboxTxIntentDetailResponse | None = None
    attempt_timeline: tuple[SandboxTxAttemptTimelineRowResponse, ...] = ()
    receipt_timeline: tuple[SandboxTxReceiptTimelineRowResponse, ...] = ()
    recent_action_logs: tuple[UiActionLogRowResponse, ...] = ()


class ReportsTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    run_id: str
    started_at: str
    finished_at: str
    total_markets: int
    candidates: int
    executed: int
    settled: int
    wins: int
    losses: int
    skipped: int
    event_count: int
    artifact_counts: tuple[ChartPointResponse, ...]
    stage_timings_ms: tuple[ChartPointResponse, ...]
    failure_categories: tuple[ChartPointResponse, ...]
    report_markdown_path: str
    report_json_path: str
    replay_shortcut: ReportShortcutResponse
    generate_report_shortcut: ReportShortcutResponse
    eval_run_shortcut: ReportShortcutResponse
    eval_window_shortcut: ReportShortcutResponse
    incidents_feed: tuple[IncidentEventResponse, ...] = ()


class IncidentsFeedResponse(UiBaseModel):
    generated_at: str
    run_id: str
    rows: tuple[IncidentEventResponse, ...]


class RunOnceActionRequest(UiBaseModel):
    run_id: str = ""
    force: bool = False


class PauseActionRequest(UiBaseModel):
    reason: str = Field(default="ui_operator_pause")


class ReviewDecisionActionRequest(UiBaseModel):
    queue_id: str
    operator_id: str
    rationale: str
    note: str = ""
    confirm: bool = False


class TxReconcileActionRequest(UiBaseModel):
    run_id: str = ""
    intent_id: str = ""
    limit: int = Field(default=100, ge=0, le=500)


class TxResubmitSafeActionRequest(UiBaseModel):
    intent_id: str
    confirm: bool = False


class AdminSettingsActionRequest(UiBaseModel):
    setting: str
    value: str
    confirm: bool = False


class UiActionLogRowResponse(UiBaseModel):
    action_id: str
    action: str
    accepted: bool
    status: str
    message: str
    created_at: str
    run_id: str = ""
    queue_id: str = ""
    intent_id: str = ""
    operator_id: str = ""
    acting_user: str = ""
    acting_role: str = ""


class ReviewQueueRowResponse(UiBaseModel):
    queue_id: str
    run_id: str
    market_id: str
    side: str
    status: str
    stake_usd: float
    confidence: float
    edge_bps: float
    expires_at: str = ""
    updated_at: str = ""


class ReviewQueueDetailResponse(UiBaseModel):
    queue_id: str
    run_id: str
    market_id: str
    side: str
    status: str
    stake_usd: float
    confidence: float
    edge_bps: float
    created_at: str
    updated_at: str
    expires_at: str = ""
    prediction_rationale: tuple[str, ...]
    risk_rationale: tuple[str, ...]
    model_rationale: tuple[str, ...]
    operator_rationale: str
    notes: tuple[str, ...]
    can_approve: bool
    can_reject: bool


class ReviewQueueTabResponse(UiBaseModel):
    generated_at: str
    run_selector: RunSelectorResponse
    available: bool
    note: str
    active_status_filter: str
    status_filters: tuple[str, ...]
    pending_count: int
    approved_count: int
    rejected_count: int
    expired_count: int
    total_count: int
    rows: tuple[ReviewQueueRowResponse, ...]
    selected_queue_id: str = ""
    selected_item: ReviewQueueDetailResponse | None = None
    recent_action_logs: tuple[UiActionLogRowResponse, ...] = ()


class SandboxTxIntentDetailResponse(UiBaseModel):
    intent_id: str
    run_id: str
    market_id: str
    review_queue_id: str
    venue: str
    side: str
    stake_usd: float
    limit_price: float
    rationale: str


class SandboxTxAttemptTimelineRowResponse(UiBaseModel):
    submitted_at: str
    confirmation_status: str
    status: str
    tx_hash: str
    nonce: int | None
    retry_count: int
    replacement_for_tx_hash: str
    replaced_by_tx_hash: str
    message: str


class SandboxTxReceiptTimelineRowResponse(UiBaseModel):
    submitted_at: str
    confirmed_at: str
    status: str
    confirmation_status: str
    tx_hash: str
    nonce: int | None
    order_id: str
    message: str


class OperatorActionResponse(UiBaseModel):
    action: str
    accepted: bool
    status: str
    message: str
    run_id: str = ""
    queue_id: str = ""
    intent_id: str = ""
    operator_id: str = ""
    acting_user: str = ""
    acting_role: str = ""
    audit_action_id: str = ""
