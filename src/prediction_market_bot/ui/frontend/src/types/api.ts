// ── Generated from src/prediction_market_bot/ui/models.py ──

// ── Shared primitives ──────────────────────────────────────────────────────

export interface ChartPoint {
  label: string
  value: number
}

export interface IncidentBanner {
  level: string
  code: string
  title: string
  detail: string
  recommendation: string
}

export interface IncidentEvent {
  timestamp: string
  run_id: string
  linked_run_id: string
  event_type: string
  severity: string
  summary: string
  component: string
  affected_target: string
  reason_code: string
  queue_id: string
  intent_id: string
  request_id: string
  market_id: string
  source: string
  run_url: string
  review_queue_url: string
  sandbox_tx_url: string
  position_url: string
  settlement_url: string
  reports_url: string
}

export interface RunOption {
  run_id: string
  label: string
}

export interface RunSelector {
  selected_run_id: string
  latest_run_id: string
  options: RunOption[]
  available: boolean
  note: string
}

// ── Auth ───────────────────────────────────────────────────────────────────

export interface AuthSession {
  authenticated: boolean
  username: string
  role: string
  session_expires_at: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  authenticated: boolean
  status: string
  message: string
  user: AuthSession
}

// ── Operator actions ───────────────────────────────────────────────────────

export interface OperatorActionResponse {
  action: string
  accepted: boolean
  status: string
  message: string
  run_id: string
  queue_id: string
  intent_id: string
  operator_id: string
  acting_user: string
  acting_role: string
  audit_action_id: string
}

export interface UiActionLogRow {
  action_id: string
  action: string
  accepted: boolean
  status: string
  message: string
  created_at: string
  run_id: string
  queue_id: string
  intent_id: string
  operator_id: string
  acting_user: string
  acting_role: string
}

// ── Action requests ────────────────────────────────────────────────────────

export interface RunOnceRequest {
  run_id?: string
  force?: boolean
}

export interface PauseRequest {
  reason?: string
}

export interface ReviewDecisionRequest {
  queue_id: string
  operator_id: string
  rationale: string
  note?: string
  confirm?: boolean
}

export interface TxReconcileRequest {
  run_id?: string
  intent_id?: string
  limit?: number
}

export interface TxResubmitSafeRequest {
  intent_id: string
  confirm?: boolean
}

export interface AdminSettingsRequest {
  setting: string
  value: string
  confirm?: boolean
}

// ── Health / Readiness ─────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  app_name: string
  environment: string
  runtime_mode: string
  execution_mode: string
  timestamp_utc: string
}

export interface RuntimeMetrics {
  live_source_failures_total: number
  review_queue_depth: number
  open_positions_count: number
  pending_settlements_count: number
  tx_pending_count: number
  tx_mined_count: number
  tx_failed_count: number
  stale_data_events_total: number
  stale_data_blocked_trades_total: number
}

export interface ReadinessCheck {
  name: string
  ok: boolean
  severity: string
  detail: string
}

export interface ReadinessResponse {
  status: string
  ready: boolean
  checks: ReadinessCheck[]
  metrics: RuntimeMetrics
}

// ── Model visibility / drift ───────────────────────────────────────────────

export interface ModelVisibility {
  runtime_mode: string
  requested_engine: string
  effective_engine: string
  gate_required: boolean
  gate_reason: string
  active_model_version: string
  model_name: string
  feature_schema_version: string
  calibration_version: string
  calibration_method: string
  active_source_set: string[]
  promoted_model_version: string
  promoted_at: string
  rollback_active: boolean
  rollback_reason: string
}

export interface DriftSignal {
  name: string
  status: string
  detail: string
  current_value: number | null
  reference_value: number | null
  threshold: number | null
}

export interface DriftAlert {
  overall_status: string
  current_run_id: string
  created_at_utc: string
  warnings: string[]
  signals: DriftSignal[]
}

export interface ShadowComparisonHistoryRow {
  run_id: string
  total_rows: number
  rows_with_model_v2: number
  rows_with_parity_warnings: number
  approval_rate_heuristic: number | null
  approval_rate_model_v2: number | null
  approval_rate_delta_model_minus_heuristic: number | null
  disagreement_buckets: ChartPoint[]
}

// ── Overview ───────────────────────────────────────────────────────────────

export interface LastRunSummary {
  run_id: string
  status: string
  started_at: string
  finished_at: string
  total_markets: number
  candidates: number
  executed: number
  settled: number
  wins: number
  losses: number
  skipped: number
  event_count: number
  available: boolean
  note: string
}

export interface OverviewTab {
  generated_at: string
  run_selector: RunSelector
  runtime_mode: string
  execution_mode: string
  operator_paused: boolean
  operator_pause_reason: string
  last_run: LastRunSummary
  review_queue_depth: number
  open_positions_count: number
  pending_settlements_count: number
  live_source_failures_total: number
  tx_pending_count: number
  tx_mined_count: number
  tx_failed_count: number
  stale_data_events_total: number
  stale_data_blocked_trades_total: number
  model_visibility: ModelVisibility
  drift_alert: DriftAlert | null
  counters_chart: ChartPoint[]
  incident_banners: IncidentBanner[]
  incidents_feed: IncidentEvent[]
}

// ── System Health ──────────────────────────────────────────────────────────

export interface StartupValidationSummary {
  ok: boolean
  error_count: number
  warning_count: number
  info_count: number
  checks: ReadinessCheck[]
}

export interface HealthcheckSummary {
  status: string
  runtime_mode: string
  execution_mode: string
  timestamp_utc: string
  metrics: RuntimeMetrics
}

export interface DbConnectivitySummary {
  status: string
  detail: string
  check_name: string
}

export interface ProviderStatusSummary {
  status: string
  market_data_provider: string
  research_provider: string
  provider_failure_policy: string
  live_market_data_enabled: boolean
  live_research_enabled: boolean
  sandbox_chain_enabled: boolean
  sandbox_submit_tx: boolean
  live_source_failures_total: number
}

export interface SystemHealthTab {
  generated_at: string
  run_selector: RunSelector
  overall_status: string
  startup_validation: StartupValidationSummary
  healthcheck: HealthcheckSummary
  db_connectivity: DbConnectivitySummary
  provider_status: ProviderStatusSummary
  review_queue_depth: number
  tx_pending_count: number
  tx_mined_count: number
  tx_failed_count: number
  pending_settlements_count: number
  stale_data_events_total: number
  stale_data_blocked_trades_total: number
  incident_banners: IncidentBanner[]
  incidents_feed: IncidentEvent[]
}

// ── Scanner ────────────────────────────────────────────────────────────────

export interface ScannerCandidateRow {
  market_id: string
  title: string
  category: string
  scan_score: number
  reasons: string[]
  liquidity_usd: number
  volume_24h_usd: number
  spread_bps: number
  hours_to_resolution: number
}

export interface ScannerTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  panel_status: string
  total_markets: number
  eligible_markets_count: number
  rejected_markets_count: number
  candidates_count: number
  avg_scan_score: number
  funnel_summary: ChartPoint[]
  top_reasons: ChartPoint[]
  rejected_reasons: ChartPoint[]
  market_context_summary: ChartPoint[]
  diagnostics_summary: ChartPoint[]
  anomalies: IncidentBanner[]
  candidates: ScannerCandidateRow[]
}

// ── Research ───────────────────────────────────────────────────────────────

export interface ResearchPacketRow {
  market_id: string
  findings_count: number
  evidence_strength: number
  disagreement_score: number
  weighted_sentiment: number
  source_types: string[]
  narrative_summary: string
}

export interface ResearchTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  panel_status: string
  packets_count: number
  findings_count: number
  avg_evidence_strength: number
  avg_disagreement_score: number
  source_failures_count: number
  coverage_summary: ChartPoint[]
  source_type_distribution: ChartPoint[]
  diagnostics_summary: ChartPoint[]
  anomalies: IncidentBanner[]
  packets: ResearchPacketRow[]
}

// ── Prediction ─────────────────────────────────────────────────────────────

export interface PredictionRow {
  market_id: string
  selected_side: string
  market_yes_prob: number
  fair_yes_prob: number
  edge_bps: number
  confidence: number
  rationale: string[]
}

export interface ReportShortcut {
  label: string
  command: string
  description: string
}

export interface PredictionTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  panel_status: string
  predictions_count: number
  avg_confidence: number
  avg_edge_bps: number
  avg_probability_gap: number
  parity_warning_count: number
  model_visibility: ModelVisibility
  calibration_summary: ChartPoint[]
  shadow_comparison: ShadowComparisonHistoryRow | null
  shadow_history: ShadowComparisonHistoryRow[]
  approval_rate_summary: ChartPoint[]
  disagreement_buckets: ChartPoint[]
  enrichment_coverage: number | null
  disagreement_vs_baseline: ChartPoint[]
  drift_alert: DriftAlert | null
  diagnostics_summary: ChartPoint[]
  anomalies: IncidentBanner[]
  side_distribution: ChartPoint[]
  rows: PredictionRow[]
}

// ── Risk ───────────────────────────────────────────────────────────────────

export interface RiskRow {
  market_id: string
  approved: boolean
  side: string
  stake_usd: number
  bankroll_fraction: number
  fractional_kelly: number
  reasoning: string[]
}

export interface RiskTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  panel_status: string
  decisions_count: number
  approved_count: number
  blocked_count: number
  avg_stake_usd: number
  proposed_stake_usd: number
  approved_stake_usd: number
  avg_portfolio_exposure_usd: number
  avg_market_exposure_usd: number
  daily_stop_triggered: boolean
  circuit_breaker_active: boolean
  guardrail_distribution: ChartPoint[]
  reason_code_distribution: ChartPoint[]
  diagnostics_summary: ChartPoint[]
  anomalies: IncidentBanner[]
  rows: RiskRow[]
}

// ── Execution ──────────────────────────────────────────────────────────────

export interface ExecutionRow {
  market_id: string
  status: string
  lifecycle_path: string
  side: string
  stake_usd: number
  fill_price: number | null
  order_id: string
  execution_mode: string
  intent_rationale: string
  review_queue_id: string
  review_status: string
  review_rationale: string
  review_queue_url: string
  sandbox_tx_url: string
  position_url: string
  message: string
}

export interface ExecutionTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  intents_count: number
  executions_count: number
  linked_review_decisions_count: number
  skipped_count: number
  submitted_count: number
  completed_count: number
  failed_count: number
  status_distribution: ChartPoint[]
  lifecycle_distribution: ChartPoint[]
  rows: ExecutionRow[]
}

// ── Settlement ─────────────────────────────────────────────────────────────

export interface SettlementRow {
  request_id: string
  market_id: string
  state: string
  resolution_status: string
  outcome_classification: string
  resolved_yes: string
  pnl_usd: number
  execution_side: string
  resolution_reason: string
  retry_count: number
  review_queue_id: string
  tx_intent_id: string
  review_queue_url: string
  sandbox_tx_url: string
  position_url: string
  updated_at: string
}

export interface SettlementPendingRow {
  request_id: string
  market_id: string
  state: string
  resolution_status: string
  resolution_reason: string
  execution_side: string
  stake_usd: number
  fill_price: number | null
  order_id: string
  retry_count: number
  review_queue_id: string
  tx_intent_id: string
  review_queue_url: string
  sandbox_tx_url: string
  position_url: string
  updated_at: string
}

export interface SettlementTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  pending_requests_count: number
  resolution_checks_count: number
  settled_count: number
  resolved_settlements_count: number
  realized_pnl_usd: number
  retry_count: number
  failure_count: number
  outcome_distribution: ChartPoint[]
  pending_rows: SettlementPendingRow[]
  resolved_rows: SettlementRow[]
  rows: SettlementRow[]
}

// ── Sandbox TX ─────────────────────────────────────────────────────────────

export interface SandboxTxIntentDetail {
  intent_id: string
  run_id: string
  market_id: string
  review_queue_id: string
  venue: string
  side: string
  stake_usd: number
  limit_price: number
  rationale: string
}

export interface SandboxTxAttemptTimelineRow {
  submitted_at: string
  confirmation_status: string
  status: string
  tx_hash: string
  nonce: number | null
  retry_count: number
  replacement_for_tx_hash: string
  replaced_by_tx_hash: string
  message: string
}

export interface SandboxTxReceiptTimelineRow {
  submitted_at: string
  confirmed_at: string
  status: string
  confirmation_status: string
  tx_hash: string
  nonce: number | null
  order_id: string
  message: string
}

export interface SandboxTxRow {
  intent_id: string
  review_queue_id: string
  market_id: string
  side: string
  execution_mode: string
  confirmation_status: string
  has_receipt: boolean
  reconcile_state: string
  tx_hash: string
  nonce: number | null
  retry_count: number
  replacement_for_tx_hash: string
  replaced_by_tx_hash: string
  review_queue_url: string
  position_url: string
  message: string
}

export interface SandboxTxTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  attempts_count: number
  receipts_count: number
  pending_count: number
  mined_count: number
  failed_count: number
  dropped_count: number
  replaced_count: number
  reconcile_backlog_count: number
  confirmation_distribution: ChartPoint[]
  rows: SandboxTxRow[]
  active_intent_id: string
  can_reconcile: boolean
  can_resubmit_safe: boolean
  action_hint: string
  selected_intent: SandboxTxIntentDetail | null
  attempt_timeline: SandboxTxAttemptTimelineRow[]
  receipt_timeline: SandboxTxReceiptTimelineRow[]
  recent_action_logs: UiActionLogRow[]
}

// ── Reports ────────────────────────────────────────────────────────────────

export interface ReportsTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  run_id: string
  started_at: string
  finished_at: string
  total_markets: number
  candidates: number
  executed: number
  settled: number
  wins: number
  losses: number
  skipped: number
  event_count: number
  artifact_counts: ChartPoint[]
  stage_timings_ms: ChartPoint[]
  failure_categories: ChartPoint[]
  report_markdown_path: string
  report_json_path: string
  run_overview_url: string
  review_queue_url: string
  sandbox_tx_url: string
  positions_url: string
  settlement_url: string
  replay_shortcut: ReportShortcut
  generate_report_shortcut: ReportShortcut
  eval_run_shortcut: ReportShortcut
  eval_window_shortcut: ReportShortcut
  incidents_feed: IncidentEvent[]
}

// ── Positions ──────────────────────────────────────────────────────────────

export interface PositionsRow {
  market_id: string
  market_title: string
  market_status: string
  hours_to_resolution: number | null
  side: string
  shares: number
  avg_entry_price: number
  cost_basis_usd: number
  mark_price: number
  market_value_usd: number
  unrealized_pnl_usd: number
  exposure_pct: number
  review_queue_id: string
  review_status: string
  tx_intent_id: string
  review_queue_url: string
  prediction_url: string
  risk_url: string
  sandbox_tx_url: string
  settlement_url: string
  updated_at: string
}

export interface PositionsTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  active_market_id: string
  open_positions_count: number
  linked_review_count: number
  linked_tx_count: number
  total_exposure_usd: number
  unrealized_pnl_usd: number
  realized_pnl_usd: number
  total_pnl_usd: number
  exposure_distribution: ChartPoint[]
  rows: PositionsRow[]
}

// ── Review Queue ───────────────────────────────────────────────────────────

export interface ReviewQueueRow {
  queue_id: string
  run_id: string
  market_id: string
  market_title: string
  side: string
  status: string
  lifecycle_state: string
  executed: boolean
  execution_status: string
  fair_yes_prob: number | null
  market_yes_prob: number | null
  stake_usd: number
  confidence: number
  edge_bps: number
  prediction_rationale_summary: string
  risk_rationale_summary: string
  evidence_coverage_summary: string
  findings_count: number
  source_coverage_ratio: number | null
  prediction_url: string
  risk_url: string
  sandbox_tx_url: string
  position_url: string
  expires_at: string
  updated_at: string
}

export interface ReviewQueueDetail {
  queue_id: string
  run_id: string
  market_id: string
  market_title: string
  side: string
  status: string
  lifecycle_state: string
  executed: boolean
  execution_status: string
  stake_usd: number
  confidence: number
  edge_bps: number
  fair_yes_prob: number | null
  market_yes_prob: number | null
  created_at: string
  updated_at: string
  expires_at: string
  findings_count: number
  source_types: string[]
  evidence_strength: number | null
  disagreement_score: number | null
  source_coverage_ratio: number | null
  freshness_hours: number | null
  contradiction_score: number | null
  source_diversity: number | null
  evidence_coverage_summary: string
  prediction_rationale: string[]
  risk_rationale: string[]
  model_rationale: string[]
  operator_rationale: string
  notes: string[]
  can_approve: boolean
  can_reject: boolean
  prediction_url: string
  risk_url: string
  sandbox_tx_url: string
  position_url: string
}

export interface ReviewQueueTab {
  generated_at: string
  run_selector: RunSelector
  available: boolean
  note: string
  active_status_filter: string
  status_filters: string[]
  pending_count: number
  approved_count: number
  executed_count: number
  rejected_count: number
  expired_count: number
  total_count: number
  lifecycle_distribution: ChartPoint[]
  rows: ReviewQueueRow[]
  selected_queue_id: string
  selected_item: ReviewQueueDetail | null
  recent_action_logs: UiActionLogRow[]
}

// ── Incidents feed ─────────────────────────────────────────────────────────

export interface IncidentsFeed {
  generated_at: string
  run_id: string
  rows: IncidentEvent[]
}

// ── SSE ────────────────────────────────────────────────────────────────────

export type SseSection =
  | 'overview'
  | 'scanner'
  | 'research'
  | 'prediction'
  | 'risk'
  | 'review-queue'
  | 'execution'
  | 'sandbox-tx'
  | 'positions'
  | 'settlement'
  | 'reports'
  | 'system'
  | 'trader-a'
  | 'trader-b'

export type TabSection = SseSection

export const TAB_SECTIONS: TabSection[] = [
  'overview',
  'scanner',
  'research',
  'prediction',
  'risk',
  'review-queue',
  'execution',
  'sandbox-tx',
  'positions',
  'settlement',
  'reports',
  'system',
  'trader-a',
  'trader-b',
]

// ── Trader Live (per-model position monitor) ────────────────────────────────

export interface TraderLivePosition {
  market_id: string
  title: string
  side: string
  shares: number
  entry_price: number
  cost_basis_usd: number
  mark_price_yes: number | null
  mark_price_no: number | null
  side_mark_price: number | null
  market_value_usd: number
  unrealized_pnl_usd: number
  slot_close_iso: string | null
  slot_start_iso: string | null
  seconds_to_close: number | null
  slot_anchor_btc: number | null
  btc_delta_usd: number | null
  currently_yes: boolean | null
  currently_winning: boolean | null
}

export interface TraderLiveResponse {
  as_of: string
  btc_spot_usd: number | null
  open_count: number
  settled_count: number
  realized_pnl_usd: number
  unrealized_pnl_usd: number
  total_exposure_usd: number
  positions: TraderLivePosition[]
}
