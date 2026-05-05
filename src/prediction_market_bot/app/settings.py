from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from prediction_market_bot.domain.enums import (
    ExecutionMode,
    ProviderFailurePolicy,
    ProviderSelection,
    RuntimeMode,
)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    payload: dict[str, str] = {}
    for key, raw_value in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        value_text = str(raw_value).strip()
        if not value_text:
            continue
        payload[key_text] = value_text
    return payload


def _as_str_seq(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    payload: list[str] = []
    for raw in value:
        text = _as_str(raw)
        if text:
            payload.append(text)
    return tuple(payload)


def _as_alias_map(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return {}
    payload: dict[str, tuple[str, ...]] = {}
    for canonical, raw_aliases in value.items():
        canonical_text = _as_str(canonical).lower()
        if not canonical_text:
            continue
        aliases: list[str] = []
        if isinstance(raw_aliases, list):
            for raw_alias in raw_aliases:
                alias_text = _as_str(raw_alias).lower()
                if alias_text:
                    aliases.append(alias_text)
        elif isinstance(raw_aliases, str):
            alias_text = _as_str(raw_aliases).lower()
            if alias_text:
                aliases.append(alias_text)
        if aliases:
            payload[canonical_text] = tuple(sorted(set(aliases)))
    return payload


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return default
    return str(value).strip()


def _parse_runtime_mode(value: Any) -> RuntimeMode:
    text = _as_str(value, "DRY_RUN_STATIC").upper().replace("-", "_")
    if text in {"DRY_RUN", "DRYRUN", "STATIC"}:
        return RuntimeMode.DRY_RUN_STATIC
    if text in {"PAPER", "PAPER_LIVE"}:
        return RuntimeMode.PAPER_LIVE
    if text in {"SANDBOX", "SANDBOX_CHAIN"}:
        return RuntimeMode.SANDBOX_CHAIN
    if text in {"DISABLED", "LIVE_DISABLED"}:
        return RuntimeMode.LIVE_DISABLED
    try:
        return RuntimeMode(text)
    except ValueError as exc:
        raise ValueError(f"Unsupported runtime mode: {text}") from exc


def _parse_provider_selection(value: Any, *, default: ProviderSelection = ProviderSelection.AUTO) -> ProviderSelection:
    text = _as_str(value, default.value).upper().replace("-", "_")
    if text in {"", "AUTO"}:
        return ProviderSelection.AUTO
    if text in {"STATIC", "MOCK"}:
        return ProviderSelection.STATIC
    if text in {"LIVE", "REALTIME"}:
        return ProviderSelection.LIVE
    return ProviderSelection(text)


def _parse_provider_failure_policy(
    value: Any,
    *,
    default: ProviderFailurePolicy = ProviderFailurePolicy.FAIL_FAST,
) -> ProviderFailurePolicy:
    text = _as_str(value, default.value).upper().replace("-", "_")
    if text in {"", "FAIL_FAST", "FAIL"}:
        return ProviderFailurePolicy.FAIL_FAST
    if text in {"FALLBACK_TO_STATIC", "FALLBACK", "STATIC_FALLBACK"}:
        return ProviderFailurePolicy.FALLBACK_TO_STATIC
    return ProviderFailurePolicy(text)


@dataclass(slots=True, frozen=True)
class RuntimeSettings:
    app_name: str = "prediction-market-bot"
    env: str = "development"
    timezone: str = "UTC"
    mode: RuntimeMode = RuntimeMode.DRY_RUN_STATIC
    market_data_provider: ProviderSelection = ProviderSelection.AUTO
    research_provider: ProviderSelection = ProviderSelection.AUTO
    provider_failure_policy: ProviderFailurePolicy = ProviderFailurePolicy.FAIL_FAST
    scan_interval_sec: int = 300
    # REC-09: knobs below are read from config to avoid silent ignore, but the
    # scheduler currently runs research and prediction sequentially (single-
    # threaded).  Values > 1 are accepted and stored; a startup warning is
    # emitted so operators know concurrency is not yet implemented.
    settlement_interval_sec: int = 600
    max_parallel_research_jobs: int = 1
    max_parallel_prediction_jobs: int = 1


@dataclass(slots=True, frozen=True)
class LoggingSettings:
    level: str = "INFO"
    json_logs: bool = True
    file_path: str = ""
    rotate_max_bytes: int = 10_485_760
    rotate_backup_count: int = 5


@dataclass(slots=True, frozen=True)
class MetricsSettings:
    enabled: bool = True
    exporter: str = "prometheus_textfile"
    path: str = "data/metrics/metrics.prom"


@dataclass(slots=True, frozen=True)
class HealthcheckSettings:
    enabled: bool = True
    path: str = "/health"


@dataclass(slots=True, frozen=True)
class PerformanceSettings:
    enable_cli_profile: bool = False
    slow_stage_threshold_ms: float = 1_500.0
    ui_poll_cache_ttl_sec: float = 1.5
    ui_poll_min_interval_sec: float = 2.0
    include_response_timing_headers: bool = True


@dataclass(slots=True, frozen=True)
class StorageSettings:
    operational_db_driver: str = "sqlite"
    operational_db_path: str = "data/runtime.db"
    operational_db_dsn: str = ""
    operational_db_dsn_env: str = "OPERATIONAL_DB_DSN"
    operational_db_backup_dir: str = "data/backups/operational-db"
    operational_db_auto_migrate_on_boot: bool = True
    operational_db_require_up_to_date: bool = True
    artifacts_dir: str = "data/artifacts"
    audit_log_path: str = "data/audit/events.jsonl"


@dataclass(slots=True, frozen=True)
class ScanSettings:
    min_liquidity_usd: float = 10_000.0
    min_volume_24h_usd: float = 5_000.0
    min_hours_to_resolution: float = 6.0
    max_spread_bps: int = 300
    anomaly_move_bps: int = 150
    btc_only_mode: bool = False


@dataclass(slots=True, frozen=True)
class PredictionSettings:
    min_confidence: float = 0.62
    min_edge_bps: int = 300
    market_weight: float = 0.45
    narrative_weight: float = 0.45
    structure_weight: float = 0.10
    engine: str = "heuristic"
    model_artifact_path: str = ""
    calibration_artifact_path: str = ""
    model_expected_feature_schema_version: str = "v1"
    strict_feature_parity: bool = True
    fallback_to_heuristic: bool = True
    forced_heuristic_reason: str = ""
    alt_shadow_enabled: bool = False
    alt_shadow_model_artifact_path: str = ""
    alt_shadow_calibration_artifact_path: str = ""
    alt_shadow_expected_feature_schema_version: str = "alt-v1"
    alt_shadow_strict_feature_parity: bool = True
    alt_shadow_required_source_coverage: tuple[str, ...] = ("news", "reddit", "x")
    alt_shadow_require_llm_enrichment: bool = True
    alt_shadow_promoted_enabled: bool = False
    alt_shadow_promoted_runtime_modes: tuple[str, ...] = ("SANDBOX_CHAIN",)
    # Regime gate: empirically-derived filter to veto high-loss patterns.
    # See agents/regime_gate.py for details.
    regime_gate_enabled: bool = False
    regime_gate_trend_follow_threshold: float = 0.003
    regime_gate_fill_price_skip_min: float = 0.45
    regime_gate_fill_price_skip_max: float = 0.48
    regime_gate_skip_extreme_rsi: bool = False
    regime_gate_skip_bad_hours_utc: bool = False
    # LLM-only engine (Bot D). When engine == "llm_only" the agent uses these.
    llm_provider: str = "groq"
    llm_endpoint_url: str = "https://api.groq.com/openai/v1/chat/completions"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_api_key_env: str = "GROQ_API_KEY"
    llm_temperature: float = 0.2
    llm_timeout_sec: float = 12.0
    llm_max_retries: int = 1


@dataclass(slots=True, frozen=True)
class ModelPromotionSettings:
    enabled: bool = True
    require_explicit_approval_in_live_modes: bool = True
    required_runtime_modes: tuple[str, ...] = ("PAPER_LIVE", "SANDBOX_CHAIN")
    promoted_runtime_modes: tuple[str, ...] = ("SANDBOX_CHAIN",)
    benchmark_min_delta_brier: float = 0.0
    benchmark_min_delta_log_loss: float = 0.0
    max_calibration_error: float = 0.08
    max_calibration_brier_increase: float = 0.01
    approval_rate_min: float = 0.01
    approval_rate_max: float = 0.80
    min_edge_capture_ratio: float = 0.50
    min_realized_vs_expected_edge_ratio: float = 0.30
    max_shadow_parity_warning_rate: float = 0.05
    drift_reference_runs: int = 10
    feature_shift_warn_threshold: float = 0.35
    market_regime_warn_threshold: float = 0.30
    research_coverage_warn_threshold: float = 0.25
    confidence_collapse_warn_threshold: float = 0.20


@dataclass(slots=True, frozen=True)
class RiskSettings:
    bankroll_usd: float = 10_000.0
    # taker_fee_bps: Polymarket taker fee in basis points.  The risk agent
    # subtracts this from the raw edge before comparing to min_edge_bps so
    # only trades with *net* edge above the threshold are approved.
    taker_fee_bps: int = 72
    fractional_kelly: float = 0.25
    max_position_pct: float = 0.02
    max_event_bucket_pct: float = 0.08
    max_category_bucket_pct: float = 0.10
    max_portfolio_exposure_pct: float = 0.10
    max_per_market_exposure_pct: float = 0.02
    max_daily_loss_pct: float = 0.05
    daily_stop_loss_pct: float = 0.05
    min_liquidity_usd: float = 10_000.0
    max_spread_bps: int = 300
    max_snapshot_age_sec: int = 900
    global_circuit_breaker: bool = False
    manual_pause: bool = False
    min_bet_usd: float = 25.0
    # Conservative caps for the initial live period (first N days)
    live_initial_period_days: int = 30
    live_initial_max_position_pct: float = 0.01
    live_initial_max_portfolio_exposure_pct: float = 0.05
    live_initial_bankroll_usd: float = 2_000.0
    live_initial_started_at: str = ""

    def effective_bankroll_usd(self, execution_mode: str) -> float:
        if self._in_initial_live_period(execution_mode):
            return self.live_initial_bankroll_usd
        return self.bankroll_usd

    def effective_max_position_pct(self, execution_mode: str) -> float:
        if self._in_initial_live_period(execution_mode):
            return self.live_initial_max_position_pct
        return self.max_position_pct

    def effective_max_portfolio_exposure_pct(self, execution_mode: str) -> float:
        if self._in_initial_live_period(execution_mode):
            return self.live_initial_max_portfolio_exposure_pct
        return self.max_portfolio_exposure_pct

    def _in_initial_live_period(self, execution_mode: str) -> bool:
        mode = execution_mode.strip().upper()
        if mode not in ("LIVE", "SANDBOX_CHAIN"):
            return False
        if not self.live_initial_started_at:
            return True  # Not started yet → use conservative caps
        try:
            from datetime import UTC, datetime as _dt
            started = _dt.fromisoformat(self.live_initial_started_at)
            return (_dt.now(UTC) - started).days < self.live_initial_period_days
        except (ValueError, TypeError):
            return True  # Parse error → be conservative


@dataclass(slots=True, frozen=True)
class HttpSettings:
    user_agent: str = "prediction-market-bot/0.1"
    contact: str = ""
    timeout_sec: float = 8.0
    max_retries: int = 2
    retry_backoff_sec: float = 0.5
    retry_jitter_sec: float = 0.25
    cache_ttl_sec: int = 600
    openalex_api_key_env: str = "OPENALEX_API_KEY"
    enforce_allowed_hosts: bool = False
    allowed_hosts: tuple[str, ...] = ()
    max_response_bytes: int = 1_048_576

    @property
    def user_agent_with_contact(self) -> str:
        contact = self.contact.strip()
        if not contact:
            return self.user_agent
        return f"{self.user_agent} ({contact})"


@dataclass(slots=True, frozen=True)
class AlertingWebhookSettings:
    enabled: bool = False
    webhook_url: str = ""
    webhook_url_env: str = "PM_BOT_ALERT_WEBHOOK_URL"
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5


@dataclass(slots=True, frozen=True)
class AlertingSlackSettings:
    enabled: bool = False
    webhook_url: str = ""
    webhook_url_env: str = "PM_BOT_SLACK_WEBHOOK_URL"
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5


@dataclass(slots=True, frozen=True)
class AlertingTelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    bot_token_env: str = "PM_BOT_TELEGRAM_BOT_TOKEN"
    chat_id: str = ""
    chat_id_env: str = "PM_BOT_TELEGRAM_CHAT_ID"
    timeout_sec: float = 5.0
    max_retries: int = 1
    retry_backoff_sec: float = 0.5


@dataclass(slots=True, frozen=True)
class AlertingSettings:
    enabled: bool = False
    dedupe_window_sec: int = 300
    repeated_live_source_failures_threshold: int = 3
    webhook: AlertingWebhookSettings = field(default_factory=AlertingWebhookSettings)
    slack: AlertingSlackSettings = field(default_factory=AlertingSlackSettings)
    telegram: AlertingTelegramSettings = field(default_factory=AlertingTelegramSettings)


@dataclass(slots=True, frozen=True)
class LiveMarketDataSettings:
    enabled: bool = False
    endpoint_url: str = "https://gamma-api.polymarket.com/markets"
    limit: int = 50
    max_staleness_sec: int = 900
    timeout_sec: float = 8.0
    max_retries: int = 2
    retry_backoff_sec: float = 0.5
    retry_jitter_sec: float = 0.25
    cache_ttl_sec: int = 0
    headers: dict[str, str] | None = None
    api_key_env: str = ""
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    require_api_key: bool = False


@dataclass(slots=True, frozen=True)
class LiveResearchSettings:
    enabled: bool = False
    wikipedia_endpoint_url: str = "https://en.wikipedia.org/w/api.php"
    openalex_endpoint_url: str = "https://api.openalex.org/works"
    limit_per_source: int = 5
    wikipedia_timeout_sec: float = 8.0
    wikipedia_max_retries: int = 2
    wikipedia_retry_backoff_sec: float = 0.5
    wikipedia_retry_jitter_sec: float = 0.25
    wikipedia_cache_ttl_sec: int = 600
    wikipedia_headers: dict[str, str] | None = None
    wikipedia_api_key_env: str = ""
    wikipedia_api_key_header: str = "Authorization"
    wikipedia_api_key_prefix: str = "Bearer "
    wikipedia_require_api_key: bool = False
    openalex_timeout_sec: float = 8.0
    openalex_max_retries: int = 2
    openalex_retry_backoff_sec: float = 0.5
    openalex_retry_jitter_sec: float = 0.25
    openalex_cache_ttl_sec: int = 600
    openalex_headers: dict[str, str] | None = None
    openalex_api_key_env: str = ""
    openalex_api_key_header: str = "Authorization"
    openalex_api_key_prefix: str = "Bearer "
    openalex_require_api_key: bool = False


@dataclass(slots=True, frozen=True)
class AltDataSourceCapabilitiesSettings:
    requires_oauth: bool = False
    requires_user_context: bool = False
    supports_backfill: bool = True
    supports_live_polling: bool = True
    supports_search: bool = True
    supports_thread_context_expansion: bool = False


@dataclass(slots=True, frozen=True)
class AltDataSourceSettings:
    source_id: str
    source_class: str
    enabled: bool = False
    adapter: str = ""
    endpoint_url: str = ""
    credential_env: str = ""
    capabilities: AltDataSourceCapabilitiesSettings = field(default_factory=AltDataSourceCapabilitiesSettings)


@dataclass(slots=True, frozen=True)
class AltDataSettings:
    enabled: bool = False
    sources: tuple[AltDataSourceSettings, ...] = ()


@dataclass(slots=True, frozen=True)
class HyperliquidSettings:
    """Settings for the Hyperliquid perp executor (Bot E).

    Modes are driven by ``execution.mode`` in app YAML:
      HYPERLIQUID_DRY_RUN, HYPERLIQUID_TESTNET, HYPERLIQUID_LIVE.

    The private key is NEVER stored in YAML — read from the env var named
    by ``private_key_env`` (default ``HL_PRIVATE_KEY``).
    """

    enabled: bool = False
    coin: str = "BTC"
    private_key_env: str = "HL_PRIVATE_KEY"
    leverage: int = 1
    take_profit_pct: float = 0.0030
    stop_loss_pct: float = 0.0020
    time_exit_seconds: int = 900
    min_order_usd: float = 10.0
    max_size_usd: float = 100.0
    default_slippage: float = 0.005


@dataclass(slots=True, frozen=True)
class PolymarketClobSettings:
    """Settings for the live Polymarket CLOB order executor.

    NEVER store ``private_key`` directly — read it from the env var named by
    ``private_key_env``.  Set ``dry_run=False`` only after a full
    SANDBOX_CHAIN dress-rehearsal has passed cleanly.
    """

    enabled: bool = False
    clob_endpoint_url: str = "https://clob.polymarket.com"
    chain_id: int = 137  # Polygon mainnet; Amoy testnet = 80002
    exchange_contract_address: str = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    private_key_env: str = "POLYMARKET_PRIVATE_KEY"
    wallet_address: str = ""
    slippage_bps: int = 50
    max_taker_fee_bps: int = 100
    timeout_sec: float = 10.0
    max_retries: int = 2
    retry_backoff_sec: float = 1.0
    # dry_run: sign orders but do NOT submit. Safe default.
    dry_run: bool = True


@dataclass(slots=True, frozen=True)
class SandboxChainSettings:
    enabled: bool = False
    rpc_url: str = ""
    chain_id: int = 80_002
    contract_address: str = ""
    from_address: str = ""
    intent_method_selector: str = "0x6e6c3d69"
    submit_tx: bool = False
    private_key_env: str = "SANDBOX_CHAIN_PRIVATE_KEY"
    allow_unlocked_send: bool = True
    gas_limit: int = 250_000
    confirmations_required: int = 1
    dropped_after_sec: int = 180
    request_timeout_sec: float = 8.0


@dataclass(slots=True, frozen=True)
class StrategyResearchSettings:
    base_dir: str = "data/strategy-research"
    default_dataset_id: str = "historical-markets"
    markets_endpoint_url: str = "https://gamma-api.polymarket.com/markets"
    events_endpoint_url: str = "https://gamma-api.polymarket.com/events/{event_id}"
    snapshots_endpoint_url: str = ""
    orderbook_endpoint_url: str = ""
    trades_endpoint_url: str = ""
    resolutions_endpoint_url: str = ""
    page_size: int = 200
    max_pages_per_run: int = 0
    throttle_sec: float = 0.2
    include_orderbook: bool = True
    include_trades: bool = True
    research_corpus_base_dir: str = "data/strategy-research/research-corpus"
    research_corpus_default_corpus_id: str = "historical-research-evidence"
    research_corpus_source_dataset_id: str = "historical-markets"
    research_corpus_limit_per_source: int = 5
    research_corpus_throttle_sec: float = 0.1
    research_corpus_require_published_at: bool = True
    research_corpus_drop_unaligned: bool = True
    research_corpus_enabled_sources: tuple[str, ...] = ()
    news_corpus_base_dir: str = "data/strategy-research/news-corpus"
    news_corpus_default_corpus_id: str = "historical-news-corpus"
    news_corpus_default_source_id: str = "news_rss_web"
    news_corpus_limit_per_query: int = 50
    news_corpus_throttle_sec: float = 0.0
    news_corpus_language: str = "en-US"
    news_corpus_region: str = "US"
    reddit_corpus_base_dir: str = "data/strategy-research/reddit-corpus"
    reddit_corpus_default_corpus_id: str = "historical-reddit-corpus"
    reddit_corpus_default_source_id: str = "reddit"
    reddit_corpus_limit_per_query: int = 50
    reddit_corpus_max_pages_per_query: int = 5
    reddit_corpus_include_comments: bool = False
    reddit_corpus_comment_limit_per_post: int = 25
    reddit_corpus_throttle_sec: float = 0.1
    x_corpus_base_dir: str = "data/strategy-research/x-corpus"
    x_corpus_default_corpus_id: str = "historical-x-corpus"
    x_corpus_default_source_id: str = "x"
    x_corpus_limit_per_query: int = 50
    x_corpus_max_pages_per_query: int = 5
    x_corpus_auth_mode: str = "bearer"
    x_corpus_search_endpoint_path: str = "/2/tweets/search/recent"
    x_corpus_user_lookup_endpoint_path_template: str = "/2/users/by/username/{username}"
    x_corpus_user_posts_endpoint_path_template: str = "/2/users/{user_id}/tweets"
    x_corpus_throttle_sec: float = 0.1
    linkage_base_dir: str = "data/strategy-research/linkage"
    linkage_default_linkage_id: str = "historical-evidence-linkage"
    linkage_source_dataset_id: str = "historical-markets"
    linkage_news_corpus_id: str = "historical-news-corpus"
    linkage_reddit_corpus_id: str = "historical-reddit-corpus"
    linkage_x_corpus_id: str = "historical-x-corpus"
    linkage_enabled_source_classes: tuple[str, ...] = ("news_rss_web", "reddit", "x")
    linkage_max_staleness_days: int = 45
    linkage_similarity_threshold: float = 0.20
    linkage_ambiguity_margin: float = 0.03
    linkage_max_candidates_per_evidence: int = 5
    linkage_alias_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    llm_enrichment_base_dir: str = "data/strategy-research/llm-enrichment"
    llm_enrichment_default_enrichment_id: str = "historical-llm-enrichment"
    llm_enrichment_default_linkage_id: str = "historical-evidence-linkage"
    llm_enrichment_news_corpus_id: str = "historical-news-corpus"
    llm_enrichment_reddit_corpus_id: str = "historical-reddit-corpus"
    llm_enrichment_x_corpus_id: str = "historical-x-corpus"
    llm_enrichment_enabled: bool = False
    llm_enrichment_provider: str = "deterministic"
    llm_enrichment_model_id: str = "deterministic-enrichment-v1"
    llm_enrichment_prompt_version: str = "v1"
    llm_enrichment_enable_fallback: bool = True
    llm_enrichment_allow_external_provider: bool = False
    llm_enrichment_include_states: tuple[str, ...] = ("linked", "ambiguous")
    llm_enrichment_throttle_sec: float = 0.0


@dataclass(slots=True, frozen=True)
class ExecutionSettings:
    mode: ExecutionMode = ExecutionMode.PAPER
    rehearsal_mode: ExecutionMode = ExecutionMode.SANDBOX_CHAIN
    enable_rehearsal_lane: bool = True
    blocking_trade_review: bool = True
    review_auto_approve: bool = False
    review_candidate_expiry_seconds: int = 0
    settlement_same_run: bool = False


@dataclass(slots=True, frozen=True)
class SecretsSettings:
    backend: str = "env"
    command_template: str = ""
    command_timeout_sec: float = 5.0
    env_fallback: bool = True
    vault_addr_env: str = "VAULT_ADDR"
    vault_token_env: str = "VAULT_TOKEN"
    vault_mount: str = "secret"
    vault_path_prefix: str = "prediction-market-bot"
    vault_timeout_sec: float = 5.0


@dataclass(slots=True, frozen=True)
class CliAuthSettings:
    enabled: bool = False
    actor_user_env: str = "PM_BOT_ACTOR_USER"
    actor_role_env: str = "PM_BOT_ACTOR_ROLE"


@dataclass(slots=True, frozen=True)
class RateLimitSettings:
    enabled: bool = True
    requests_per_second: float = 10.0
    burst: int = 30


@dataclass(slots=True, frozen=True)
class SecuritySettings:
    secrets_from_env: bool = True
    redact_secrets_in_logs: bool = True
    require_explicit_live_flag: bool = True
    secure_headers_enabled: bool = True
    secrets: SecretsSettings = field(default_factory=SecretsSettings)
    cli_auth: CliAuthSettings = field(default_factory=CliAuthSettings)
    rate_limit: RateLimitSettings = field(default_factory=RateLimitSettings)


@dataclass(slots=True, frozen=True)
class UiAuthUserSettings:
    username: str
    role: str
    password: str = ""
    password_env: str = ""
    password_hash: str = ""
    password_hash_env: str = ""


@dataclass(slots=True, frozen=True)
class UiAuthSettings:
    enabled: bool = False
    session_secret_env: str = "PM_BOT_UI_SESSION_SECRET"
    session_timeout_sec: int = 1800
    cookie_name: str = "pm_bot_ui_session"
    cookie_secure: bool = True
    cookie_samesite: str = "lax"
    require_password_hashes: bool = False
    max_failed_attempts: int = 5
    lockout_seconds: int = 300
    users: tuple[UiAuthUserSettings, ...] = ()


@dataclass(slots=True, frozen=True)
class AppSettings:
    runtime: RuntimeSettings
    logging: LoggingSettings
    metrics: MetricsSettings
    healthcheck: HealthcheckSettings
    performance: PerformanceSettings
    storage: StorageSettings
    venue: str
    dry_run: bool
    allow_live_execution: bool
    enable_manual_review_queue: bool
    scan: ScanSettings
    prediction: PredictionSettings
    risk: RiskSettings
    execution: ExecutionSettings
    model_promotion: ModelPromotionSettings
    http: HttpSettings
    alerting: AlertingSettings
    live_market_data: LiveMarketDataSettings
    live_research: LiveResearchSettings
    alt_data: AltDataSettings
    polymarket_clob: PolymarketClobSettings
    sandbox_chain: SandboxChainSettings
    hyperliquid: HyperliquidSettings
    strategy_research: StrategyResearchSettings
    ui_auth: UiAuthSettings
    security: SecuritySettings

    @classmethod
    def from_dicts(cls, app_config: Mapping[str, Any], agents_config: Mapping[str, Any]) -> "AppSettings":
        app_section = _as_dict(app_config.get("app"))
        runtime_section = _as_dict(app_config.get("runtime"))
        venue_section = _as_dict(app_config.get("venue"))
        execution_section = _as_dict(app_config.get("execution"))
        feature_flags = _as_dict(app_config.get("feature_flags"))
        observability = _as_dict(app_config.get("observability"))
        metrics_section = _as_dict(observability.get("metrics"))
        healthcheck_section = _as_dict(observability.get("healthcheck"))
        performance_section = _as_dict(observability.get("performance"))
        logs_section = _as_dict(observability.get("logs"))
        storage_section = _as_dict(app_config.get("storage"))
        audit_log_section = _as_dict(storage_section.get("audit_log"))
        http_section = _as_dict(app_config.get("http"))
        alerting_section = _as_dict(app_config.get("alerting"))
        alerting_webhook_section = _as_dict(alerting_section.get("webhook"))
        alerting_slack_section = _as_dict(alerting_section.get("slack"))
        alerting_telegram_section = _as_dict(alerting_section.get("telegram"))
        live_market_section = _as_dict(app_config.get("live_market_data"))
        live_research_section = _as_dict(app_config.get("live_research"))
        alt_data_section = _as_dict(app_config.get("alt_data"))
        alt_data_sources_section = _as_dict(alt_data_section.get("sources"))
        legacy_research_sources = _as_dict(app_config.get("research_sources"))
        wikipedia_research_section = _as_dict(live_research_section.get("wikipedia"))
        openalex_research_section = _as_dict(live_research_section.get("openalex"))
        sandbox_chain_section = _as_dict(app_config.get("sandbox_chain"))
        polymarket_clob_section = _as_dict(app_config.get("polymarket_clob"))
        strategy_research_section = _as_dict(app_config.get("strategy_research"))
        historical_ingest_section = _as_dict(strategy_research_section.get("historical_data_ingest"))
        research_corpus_section = _as_dict(strategy_research_section.get("research_corpus"))
        news_corpus_section = _as_dict(strategy_research_section.get("news_corpus"))
        reddit_corpus_section = _as_dict(strategy_research_section.get("reddit_corpus"))
        x_corpus_section = _as_dict(strategy_research_section.get("x_corpus"))
        linkage_section = _as_dict(strategy_research_section.get("linkage"))
        llm_enrichment_section = _as_dict(strategy_research_section.get("llm_enrichment"))
        model_promotion_section = _as_dict(app_config.get("model_promotion"))
        ui_auth_section = _as_dict(app_config.get("ui_auth"))
        security_section = _as_dict(app_config.get("security"))
        secrets_section = _as_dict(security_section.get("secrets"))
        cli_auth_section = _as_dict(security_section.get("cli_auth"))
        rate_limit_section = _as_dict(security_section.get("rate_limit"))

        thresholds = _as_dict(agents_config.get("thresholds"))
        risk_section = _as_dict(agents_config.get("risk"))
        prediction_agent = _as_dict(_as_dict(agents_config.get("agents")).get("prediction"))
        components = _as_dict(prediction_agent.get("components"))
        model_inference = _as_dict(prediction_agent.get("model_inference"))

        run_loop_section = _as_dict(app_section.get("run_loop"))
        runtime_mode = _parse_runtime_mode(runtime_section.get("mode", app_section.get("mode", "DRY_RUN_STATIC")))
        runtime = RuntimeSettings(
            app_name=str(app_section.get("name", "prediction-market-bot")),
            env=str(app_section.get("env", "development")),
            timezone=str(app_section.get("timezone", "UTC")),
            mode=runtime_mode,
            market_data_provider=_parse_provider_selection(
                runtime_section.get(
                    "market_data_provider",
                    "AUTO",
                )
            ),
            research_provider=_parse_provider_selection(
                runtime_section.get(
                    "research_provider",
                    "AUTO",
                )
            ),
            provider_failure_policy=_parse_provider_failure_policy(
                runtime_section.get("provider_failure_policy", "FAIL_FAST")
            ),
            scan_interval_sec=max(int(run_loop_section.get("scan_interval_sec", 300)), 1),
            settlement_interval_sec=max(int(run_loop_section.get("settlement_interval_sec", 600)), 1),
            max_parallel_research_jobs=max(int(run_loop_section.get("max_parallel_research_jobs", 1)), 1),
            max_parallel_prediction_jobs=max(int(run_loop_section.get("max_parallel_prediction_jobs", 1)), 1),
        )
        logging_settings = LoggingSettings(
            level=str(observability.get("log_level", "INFO")),
            json_logs=_as_bool(observability.get("json_logs"), True),
            file_path=str(logs_section.get("file_path", "")),
            rotate_max_bytes=max(int(logs_section.get("rotate_max_bytes", 10_485_760)), 1),
            rotate_backup_count=max(int(logs_section.get("rotate_backup_count", 5)), 1),
        )
        metrics_settings = MetricsSettings(
            enabled=_as_bool(metrics_section.get("enabled"), True),
            exporter=str(metrics_section.get("exporter", "prometheus_textfile")),
            path=str(metrics_section.get("path", "data/metrics/metrics.prom")),
        )
        healthcheck_settings = HealthcheckSettings(
            enabled=_as_bool(healthcheck_section.get("enabled"), True),
            path=str(healthcheck_section.get("path", "/health")),
        )
        performance_settings = PerformanceSettings(
            enable_cli_profile=_as_bool(performance_section.get("enable_cli_profile"), False),
            slow_stage_threshold_ms=max(float(performance_section.get("slow_stage_threshold_ms", 1_500.0)), 0.0),
            ui_poll_cache_ttl_sec=max(float(performance_section.get("ui_poll_cache_ttl_sec", 1.5)), 0.0),
            ui_poll_min_interval_sec=max(float(performance_section.get("ui_poll_min_interval_sec", 2.0)), 0.1),
            include_response_timing_headers=_as_bool(
                performance_section.get("include_response_timing_headers"),
                True,
            ),
        )
        operational_db_section = _as_dict(storage_section.get("operational_db"))
        storage_settings = StorageSettings(
            operational_db_driver=str(operational_db_section.get("driver", "sqlite")),
            operational_db_path=str(operational_db_section.get("path", "data/runtime.db")),
            operational_db_dsn=str(operational_db_section.get("dsn", "")),
            operational_db_dsn_env=str(operational_db_section.get("dsn_env", "OPERATIONAL_DB_DSN")),
            operational_db_backup_dir=str(
                operational_db_section.get("backup_dir", "data/backups/operational-db")
            ),
            operational_db_auto_migrate_on_boot=_as_bool(
                operational_db_section.get("auto_migrate_on_boot"),
                True,
            ),
            operational_db_require_up_to_date=_as_bool(
                operational_db_section.get("require_up_to_date"),
                True,
            ),
            artifacts_dir=str(storage_section.get("artifacts_dir", "data/artifacts")),
            audit_log_path=str(audit_log_section.get("path", "data/audit/events.jsonl")),
        )
        scan = ScanSettings(
            min_liquidity_usd=float(thresholds.get("min_liquidity_usd", 10_000.0)),
            min_volume_24h_usd=float(thresholds.get("min_volume_24h_usd", 5_000.0)),
            min_hours_to_resolution=float(thresholds.get("min_hours_to_resolution", 6.0)),
            max_spread_bps=int(thresholds.get("max_spread_bps", 300)),
            btc_only_mode=_as_bool(thresholds.get("btc_only_mode"), False),
        )
        alt_shadow_inference = _as_dict(model_inference.get("alt_shadow"))
        prediction = PredictionSettings(
            min_confidence=float(thresholds.get("min_confidence", 0.62)),
            min_edge_bps=int(thresholds.get("min_edge_bps", 300)),
            market_weight=float(components.get("market_weight", 0.45)),
            narrative_weight=float(components.get("narrative_weight", 0.45)),
            structure_weight=float(components.get("structure_weight", 0.10)),
            engine=_as_str(model_inference.get("engine"), "heuristic").lower(),
            model_artifact_path=_as_str(model_inference.get("model_artifact_path")),
            calibration_artifact_path=_as_str(model_inference.get("calibration_artifact_path")),
            model_expected_feature_schema_version=_as_str(
                model_inference.get("feature_schema_version"),
                "v1",
            ),
            strict_feature_parity=_as_bool(model_inference.get("strict_feature_parity"), True),
            fallback_to_heuristic=_as_bool(model_inference.get("fallback_to_heuristic"), True),
            forced_heuristic_reason=_as_str(model_inference.get("forced_heuristic_reason")),
            alt_shadow_enabled=_as_bool(alt_shadow_inference.get("enabled"), False),
            alt_shadow_model_artifact_path=_as_str(
                alt_shadow_inference.get("model_artifact_path")
            ),
            alt_shadow_calibration_artifact_path=_as_str(
                alt_shadow_inference.get("calibration_artifact_path")
            ),
            alt_shadow_expected_feature_schema_version=_as_str(
                alt_shadow_inference.get("feature_schema_version"),
                "alt-v1",
            ),
            alt_shadow_strict_feature_parity=_as_bool(
                alt_shadow_inference.get("strict_feature_parity"),
                True,
            ),
            alt_shadow_required_source_coverage=_as_str_seq(
                alt_shadow_inference.get("required_source_coverage") or ["news", "reddit", "x"]
            ),
            alt_shadow_require_llm_enrichment=_as_bool(
                alt_shadow_inference.get("require_llm_enrichment"),
                True,
            ),
            alt_shadow_promoted_enabled=_as_bool(
                alt_shadow_inference.get("promoted_enabled"),
                False,
            ),
            alt_shadow_promoted_runtime_modes=_as_str_seq(
                alt_shadow_inference.get("promoted_runtime_modes") or ["SANDBOX_CHAIN"]
            ),
            regime_gate_enabled=_as_bool(
                model_inference.get("regime_gate_enabled"), False
            ),
            regime_gate_trend_follow_threshold=float(
                model_inference.get("regime_gate_trend_follow_threshold", 0.003)
            ),
            regime_gate_fill_price_skip_min=float(
                model_inference.get("regime_gate_fill_price_skip_min", 0.45)
            ),
            regime_gate_fill_price_skip_max=float(
                model_inference.get("regime_gate_fill_price_skip_max", 0.48)
            ),
            regime_gate_skip_extreme_rsi=_as_bool(
                model_inference.get("regime_gate_skip_extreme_rsi"), False
            ),
            regime_gate_skip_bad_hours_utc=_as_bool(
                model_inference.get("regime_gate_skip_bad_hours_utc"), False
            ),
            llm_provider=_as_str(model_inference.get("llm_provider"), "groq"),
            llm_endpoint_url=_as_str(
                model_inference.get("llm_endpoint_url"),
                "https://api.groq.com/openai/v1/chat/completions",
            ),
            llm_model=_as_str(model_inference.get("llm_model"), "llama-3.3-70b-versatile"),
            llm_api_key_env=_as_str(model_inference.get("llm_api_key_env"), "GROQ_API_KEY"),
            llm_temperature=float(model_inference.get("llm_temperature", 0.2)),
            llm_timeout_sec=float(model_inference.get("llm_timeout_sec", 12.0)),
            llm_max_retries=int(model_inference.get("llm_max_retries", 1)),
        )
        risk = RiskSettings(
            bankroll_usd=float(risk_section.get("bankroll_usd", 10_000.0)),
            taker_fee_bps=max(int(risk_section.get("taker_fee_bps", 72)), 0),
            fractional_kelly=float(risk_section.get("fractional_kelly", 0.25)),
            max_position_pct=float(risk_section.get("max_position_pct", 0.02)),
            max_event_bucket_pct=float(risk_section.get("max_event_bucket_pct", 0.08)),
            max_category_bucket_pct=float(risk_section.get("max_category_bucket_pct", 0.10)),
            max_portfolio_exposure_pct=float(
                risk_section.get(
                    "max_portfolio_exposure_pct",
                    risk_section.get("max_category_bucket_pct", 0.10),
                )
            ),
            max_per_market_exposure_pct=float(
                risk_section.get(
                    "max_per_market_exposure_pct",
                    risk_section.get("max_position_pct", 0.02),
                )
            ),
            max_daily_loss_pct=float(risk_section.get("max_daily_loss_pct", 0.05)),
            daily_stop_loss_pct=float(
                risk_section.get("daily_stop_loss_pct", risk_section.get("max_daily_loss_pct", 0.05))
            ),
            min_liquidity_usd=float(thresholds.get("min_liquidity_usd", 10_000.0)),
            max_spread_bps=int(thresholds.get("max_spread_bps", 300)),
            max_snapshot_age_sec=int(risk_section.get("max_snapshot_age_sec", 900)),
            global_circuit_breaker=_as_bool(risk_section.get("global_circuit_breaker"), False),
            manual_pause=_as_bool(risk_section.get("manual_pause"), False),
            min_bet_usd=float(risk_section.get("min_bet_usd", 25.0)),
        )

        execution_mode_default = {
            RuntimeMode.DRY_RUN_STATIC: ExecutionMode.PAPER,
            RuntimeMode.PAPER_LIVE: ExecutionMode.PAPER,
            RuntimeMode.SANDBOX_CHAIN: ExecutionMode.SANDBOX_CHAIN,
            RuntimeMode.LIVE_DISABLED: ExecutionMode.LIVE_DISABLED,
        }[runtime_mode]
        execution_mode_raw = str(execution_section.get("mode", execution_mode_default.value)).strip().upper()
        rehearsal_mode_raw = str(execution_section.get("rehearsal_mode", "SANDBOX_CHAIN")).strip().upper()
        execution = ExecutionSettings(
            mode=ExecutionMode(execution_mode_raw),
            rehearsal_mode=ExecutionMode(rehearsal_mode_raw),
            enable_rehearsal_lane=_as_bool(execution_section.get("enable_rehearsal_lane"), True),
            blocking_trade_review=_as_bool(
                execution_section.get(
                    "blocking_trade_review",
                    feature_flags.get("blocking_trade_review", True),
                ),
                True,
            ),
            review_auto_approve=_as_bool(
                execution_section.get(
                    "review_auto_approve",
                    execution_section.get("auto_approve_trade_review", False),
                ),
                False,
            ),
            review_candidate_expiry_seconds=max(
                int(
                    execution_section.get(
                        "review_candidate_expiry_seconds",
                        execution_section.get("review_expiry_seconds", 0),
                    )
                ),
                0,
            ),
            settlement_same_run=_as_bool(
                execution_section.get(
                    "settlement_same_run",
                    feature_flags.get("enable_settlement", False),
                ),
                False,
            ),
        )

        http = HttpSettings(
            user_agent=str(http_section.get("user_agent", "prediction-market-bot/0.1")),
            contact=str(http_section.get("contact", "")),
            timeout_sec=float(http_section.get("timeout_sec", 8.0)),
            max_retries=int(http_section.get("max_retries", 2)),
            retry_backoff_sec=float(http_section.get("retry_backoff_sec", 0.5)),
            retry_jitter_sec=float(http_section.get("retry_jitter_sec", 0.25)),
            cache_ttl_sec=int(http_section.get("cache_ttl_sec", 600)),
            openalex_api_key_env=str(http_section.get("openalex_api_key_env", "OPENALEX_API_KEY")),
            enforce_allowed_hosts=_as_bool(http_section.get("enforce_allowed_hosts"), False),
            allowed_hosts=_as_str_seq(http_section.get("allowed_hosts")),
            max_response_bytes=max(int(http_section.get("max_response_bytes", 1_048_576)), 1),
        )
        alerting = AlertingSettings(
            enabled=_as_bool(alerting_section.get("enabled"), False),
            dedupe_window_sec=max(int(alerting_section.get("dedupe_window_sec", 300)), 1),
            repeated_live_source_failures_threshold=max(
                int(alerting_section.get("repeated_live_source_failures_threshold", 3)),
                1,
            ),
            webhook=AlertingWebhookSettings(
                enabled=_as_bool(alerting_webhook_section.get("enabled"), False),
                webhook_url=_as_str(alerting_webhook_section.get("webhook_url")),
                webhook_url_env=_as_str(
                    alerting_webhook_section.get("webhook_url_env"),
                    "PM_BOT_ALERT_WEBHOOK_URL",
                ),
                timeout_sec=max(float(alerting_webhook_section.get("timeout_sec", 5.0)), 0.1),
                max_retries=max(int(alerting_webhook_section.get("max_retries", 1)), 0),
                retry_backoff_sec=max(float(alerting_webhook_section.get("retry_backoff_sec", 0.5)), 0.0),
            ),
            slack=AlertingSlackSettings(
                enabled=_as_bool(alerting_slack_section.get("enabled"), False),
                webhook_url=_as_str(alerting_slack_section.get("webhook_url")),
                webhook_url_env=_as_str(
                    alerting_slack_section.get("webhook_url_env"),
                    "PM_BOT_SLACK_WEBHOOK_URL",
                ),
                timeout_sec=max(float(alerting_slack_section.get("timeout_sec", 5.0)), 0.1),
                max_retries=max(int(alerting_slack_section.get("max_retries", 1)), 0),
                retry_backoff_sec=max(float(alerting_slack_section.get("retry_backoff_sec", 0.5)), 0.0),
            ),
            telegram=AlertingTelegramSettings(
                enabled=_as_bool(alerting_telegram_section.get("enabled"), False),
                bot_token=_as_str(alerting_telegram_section.get("bot_token")),
                bot_token_env=_as_str(
                    alerting_telegram_section.get("bot_token_env"),
                    "PM_BOT_TELEGRAM_BOT_TOKEN",
                ),
                chat_id=_as_str(alerting_telegram_section.get("chat_id")),
                chat_id_env=_as_str(
                    alerting_telegram_section.get("chat_id_env"),
                    "PM_BOT_TELEGRAM_CHAT_ID",
                ),
                timeout_sec=max(float(alerting_telegram_section.get("timeout_sec", 5.0)), 0.1),
                max_retries=max(int(alerting_telegram_section.get("max_retries", 1)), 0),
                retry_backoff_sec=max(float(alerting_telegram_section.get("retry_backoff_sec", 0.5)), 0.0),
            ),
        )
        model_promotion = ModelPromotionSettings(
            enabled=_as_bool(model_promotion_section.get("enabled"), True),
            require_explicit_approval_in_live_modes=_as_bool(
                model_promotion_section.get("require_explicit_approval_in_live_modes"),
                True,
            ),
            required_runtime_modes=_as_str_seq(
                model_promotion_section.get("required_runtime_modes")
            )
            or ("PAPER_LIVE", "SANDBOX_CHAIN"),
            promoted_runtime_modes=_as_str_seq(
                model_promotion_section.get("promoted_runtime_modes")
            )
            or ("SANDBOX_CHAIN",),
            benchmark_min_delta_brier=float(model_promotion_section.get("benchmark_min_delta_brier", 0.0)),
            benchmark_min_delta_log_loss=float(model_promotion_section.get("benchmark_min_delta_log_loss", 0.0)),
            max_calibration_error=max(float(model_promotion_section.get("max_calibration_error", 0.08)), 0.0),
            max_calibration_brier_increase=float(
                model_promotion_section.get("max_calibration_brier_increase", 0.01)
            ),
            approval_rate_min=max(float(model_promotion_section.get("approval_rate_min", 0.01)), 0.0),
            approval_rate_max=min(max(float(model_promotion_section.get("approval_rate_max", 0.80)), 0.0), 1.0),
            min_edge_capture_ratio=float(model_promotion_section.get("min_edge_capture_ratio", 0.50)),
            min_realized_vs_expected_edge_ratio=float(
                model_promotion_section.get("min_realized_vs_expected_edge_ratio", 0.30)
            ),
            max_shadow_parity_warning_rate=max(
                float(model_promotion_section.get("max_shadow_parity_warning_rate", 0.05)),
                0.0,
            ),
            drift_reference_runs=max(int(model_promotion_section.get("drift_reference_runs", 10)), 1),
            feature_shift_warn_threshold=max(
                float(model_promotion_section.get("feature_shift_warn_threshold", 0.35)),
                0.0,
            ),
            market_regime_warn_threshold=max(
                float(model_promotion_section.get("market_regime_warn_threshold", 0.30)),
                0.0,
            ),
            research_coverage_warn_threshold=max(
                float(model_promotion_section.get("research_coverage_warn_threshold", 0.25)),
                0.0,
            ),
            confidence_collapse_warn_threshold=max(
                float(model_promotion_section.get("confidence_collapse_warn_threshold", 0.20)),
                0.0,
            ),
        )

        live_market_data = LiveMarketDataSettings(
            enabled=_as_bool(
                live_market_section.get(
                    "enabled",
                    feature_flags.get("use_live_market_data", False),
                ),
                False,
            ),
            endpoint_url=str(live_market_section.get("endpoint_url", "https://gamma-api.polymarket.com/markets")),
            limit=int(live_market_section.get("limit", 50)),
            max_staleness_sec=int(live_market_section.get("max_staleness_sec", 900)),
            timeout_sec=float(live_market_section.get("timeout_sec", http.timeout_sec)),
            max_retries=int(live_market_section.get("max_retries", http.max_retries)),
            retry_backoff_sec=float(live_market_section.get("retry_backoff_sec", http.retry_backoff_sec)),
            retry_jitter_sec=float(live_market_section.get("retry_jitter_sec", http.retry_jitter_sec)),
            cache_ttl_sec=int(live_market_section.get("cache_ttl_sec", 0)),
            headers=_as_str_map(live_market_section.get("headers")),
            api_key_env=str(live_market_section.get("api_key_env", "")),
            api_key_header=str(live_market_section.get("api_key_header", "Authorization")),
            api_key_prefix=str(live_market_section.get("api_key_prefix", "Bearer ")),
            require_api_key=_as_bool(live_market_section.get("require_api_key"), False),
        )
        live_research = LiveResearchSettings(
            enabled=_as_bool(
                live_research_section.get(
                    "enabled",
                    feature_flags.get("use_live_research", False),
                ),
                False,
            ),
            wikipedia_endpoint_url=str(
                wikipedia_research_section.get(
                    "endpoint_url",
                    live_research_section.get("wikipedia_endpoint_url", "https://en.wikipedia.org/w/api.php"),
                )
            ),
            openalex_endpoint_url=str(
                openalex_research_section.get(
                    "endpoint_url",
                    live_research_section.get("openalex_endpoint_url", "https://api.openalex.org/works"),
                )
            ),
            limit_per_source=int(live_research_section.get("limit_per_source", 5)),
            wikipedia_timeout_sec=float(wikipedia_research_section.get("timeout_sec", http.timeout_sec)),
            wikipedia_max_retries=int(wikipedia_research_section.get("max_retries", http.max_retries)),
            wikipedia_retry_backoff_sec=float(
                wikipedia_research_section.get("retry_backoff_sec", http.retry_backoff_sec)
            ),
            wikipedia_retry_jitter_sec=float(
                wikipedia_research_section.get("retry_jitter_sec", http.retry_jitter_sec)
            ),
            wikipedia_cache_ttl_sec=int(wikipedia_research_section.get("cache_ttl_sec", http.cache_ttl_sec)),
            wikipedia_headers=_as_str_map(wikipedia_research_section.get("headers")),
            wikipedia_api_key_env=str(wikipedia_research_section.get("api_key_env", "")),
            wikipedia_api_key_header=str(wikipedia_research_section.get("api_key_header", "Authorization")),
            wikipedia_api_key_prefix=str(wikipedia_research_section.get("api_key_prefix", "Bearer ")),
            wikipedia_require_api_key=_as_bool(wikipedia_research_section.get("require_api_key"), False),
            openalex_timeout_sec=float(openalex_research_section.get("timeout_sec", http.timeout_sec)),
            openalex_max_retries=int(openalex_research_section.get("max_retries", http.max_retries)),
            openalex_retry_backoff_sec=float(openalex_research_section.get("retry_backoff_sec", http.retry_backoff_sec)),
            openalex_retry_jitter_sec=float(openalex_research_section.get("retry_jitter_sec", http.retry_jitter_sec)),
            openalex_cache_ttl_sec=int(openalex_research_section.get("cache_ttl_sec", http.cache_ttl_sec)),
            openalex_headers=_as_str_map(openalex_research_section.get("headers")),
            openalex_api_key_env=str(
                openalex_research_section.get("api_key_env", http_section.get("openalex_api_key_env", ""))
            ),
            openalex_api_key_header=str(openalex_research_section.get("api_key_header", "Authorization")),
            openalex_api_key_prefix=str(openalex_research_section.get("api_key_prefix", "Bearer ")),
            openalex_require_api_key=_as_bool(openalex_research_section.get("require_api_key"), False),
        )
        alt_data = AltDataSettings(
            enabled=_as_bool(alt_data_section.get("enabled"), False),
            sources=_parse_alt_data_sources(
                alt_data_sources_section=alt_data_sources_section,
                legacy_sources_section=legacy_research_sources,
            ),
        )
        polymarket_clob = PolymarketClobSettings(
            enabled=_as_bool(polymarket_clob_section.get("enabled"), False),
            clob_endpoint_url=_as_str(
                polymarket_clob_section.get("clob_endpoint_url"),
                "https://clob.polymarket.com",
            ),
            chain_id=int(polymarket_clob_section.get("chain_id", 137)),
            exchange_contract_address=_as_str(
                polymarket_clob_section.get("exchange_contract_address"),
                "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
            ),
            private_key_env=_as_str(
                polymarket_clob_section.get("private_key_env"),
                "POLYMARKET_PRIVATE_KEY",
            ),
            wallet_address=_as_str(polymarket_clob_section.get("wallet_address")),
            slippage_bps=max(int(polymarket_clob_section.get("slippage_bps", 50)), 0),
            max_taker_fee_bps=max(int(polymarket_clob_section.get("max_taker_fee_bps", 100)), 0),
            timeout_sec=max(float(polymarket_clob_section.get("timeout_sec", 10.0)), 0.1),
            max_retries=max(int(polymarket_clob_section.get("max_retries", 2)), 0),
            retry_backoff_sec=max(float(polymarket_clob_section.get("retry_backoff_sec", 1.0)), 0.0),
            dry_run=_as_bool(polymarket_clob_section.get("dry_run"), True),
        )
        hyperliquid_section = _as_dict(app_config.get("hyperliquid"))
        hyperliquid = HyperliquidSettings(
            enabled=_as_bool(hyperliquid_section.get("enabled"), False),
            coin=_as_str(hyperliquid_section.get("coin"), "BTC"),
            private_key_env=_as_str(hyperliquid_section.get("private_key_env"), "HL_PRIVATE_KEY"),
            leverage=max(int(hyperliquid_section.get("leverage", 1)), 1),
            take_profit_pct=max(float(hyperliquid_section.get("take_profit_pct", 0.0030)), 0.0),
            stop_loss_pct=max(float(hyperliquid_section.get("stop_loss_pct", 0.0020)), 0.0),
            time_exit_seconds=max(int(hyperliquid_section.get("time_exit_seconds", 900)), 0),
            min_order_usd=max(float(hyperliquid_section.get("min_order_usd", 10.0)), 0.0),
            max_size_usd=max(float(hyperliquid_section.get("max_size_usd", 100.0)), 0.0),
            default_slippage=max(float(hyperliquid_section.get("default_slippage", 0.005)), 0.0),
        )
        sandbox_chain = SandboxChainSettings(
            enabled=_as_bool(sandbox_chain_section.get("enabled"), False),
            rpc_url=str(sandbox_chain_section.get("rpc_url", "")),
            chain_id=int(sandbox_chain_section.get("chain_id", 80_002)),
            contract_address=str(sandbox_chain_section.get("contract_address", "")),
            from_address=str(sandbox_chain_section.get("from_address", "")),
            intent_method_selector=str(sandbox_chain_section.get("intent_method_selector", "0x6e6c3d69")),
            submit_tx=_as_bool(sandbox_chain_section.get("submit_tx"), False),
            private_key_env=str(sandbox_chain_section.get("private_key_env", "SANDBOX_CHAIN_PRIVATE_KEY")),
            allow_unlocked_send=_as_bool(sandbox_chain_section.get("allow_unlocked_send"), True),
            gas_limit=int(sandbox_chain_section.get("gas_limit", 250_000)),
            confirmations_required=int(sandbox_chain_section.get("confirmations_required", 1)),
            dropped_after_sec=int(sandbox_chain_section.get("dropped_after_sec", 180)),
            request_timeout_sec=float(sandbox_chain_section.get("request_timeout_sec", 8.0)),
        )
        strategy_research = StrategyResearchSettings(
            base_dir=_as_str(historical_ingest_section.get("base_dir"), "data/strategy-research"),
            default_dataset_id=_as_str(historical_ingest_section.get("default_dataset_id"), "historical-markets"),
            markets_endpoint_url=_as_str(
                historical_ingest_section.get("markets_endpoint_url"),
                "https://gamma-api.polymarket.com/markets",
            ),
            events_endpoint_url=_as_str(
                historical_ingest_section.get("events_endpoint_url"),
                "https://gamma-api.polymarket.com/events/{event_id}",
            ),
            snapshots_endpoint_url=_as_str(historical_ingest_section.get("snapshots_endpoint_url")),
            orderbook_endpoint_url=_as_str(historical_ingest_section.get("orderbook_endpoint_url")),
            trades_endpoint_url=_as_str(historical_ingest_section.get("trades_endpoint_url")),
            resolutions_endpoint_url=_as_str(historical_ingest_section.get("resolutions_endpoint_url")),
            page_size=max(int(historical_ingest_section.get("page_size", 200)), 1),
            max_pages_per_run=max(int(historical_ingest_section.get("max_pages_per_run", 0)), 0),
            throttle_sec=max(float(historical_ingest_section.get("throttle_sec", 0.2)), 0.0),
            include_orderbook=_as_bool(historical_ingest_section.get("include_orderbook"), True),
            include_trades=_as_bool(historical_ingest_section.get("include_trades"), True),
            research_corpus_base_dir=_as_str(
                research_corpus_section.get("base_dir"),
                "data/strategy-research/research-corpus",
            ),
            research_corpus_default_corpus_id=_as_str(
                research_corpus_section.get("default_corpus_id"),
                "historical-research-evidence",
            ),
            research_corpus_source_dataset_id=_as_str(
                research_corpus_section.get("source_dataset_id"),
                _as_str(historical_ingest_section.get("default_dataset_id"), "historical-markets"),
            ),
            research_corpus_limit_per_source=max(int(research_corpus_section.get("limit_per_source", 5)), 1),
            research_corpus_throttle_sec=max(float(research_corpus_section.get("throttle_sec", 0.1)), 0.0),
            research_corpus_require_published_at=_as_bool(research_corpus_section.get("require_published_at"), True),
            research_corpus_drop_unaligned=_as_bool(research_corpus_section.get("drop_unaligned"), True),
            research_corpus_enabled_sources=_as_str_seq(research_corpus_section.get("enabled_sources")),
            news_corpus_base_dir=_as_str(
                news_corpus_section.get("base_dir"),
                "data/strategy-research/news-corpus",
            ),
            news_corpus_default_corpus_id=_as_str(
                news_corpus_section.get("default_corpus_id"),
                "historical-news-corpus",
            ),
            news_corpus_default_source_id=_as_str(
                news_corpus_section.get("source_id"),
                "news_rss_web",
            ),
            news_corpus_limit_per_query=max(int(news_corpus_section.get("limit_per_query", 50)), 1),
            news_corpus_throttle_sec=max(float(news_corpus_section.get("throttle_sec", 0.0)), 0.0),
            news_corpus_language=_as_str(news_corpus_section.get("language"), "en-US"),
            news_corpus_region=_as_str(news_corpus_section.get("region"), "US"),
            reddit_corpus_base_dir=_as_str(
                reddit_corpus_section.get("base_dir"),
                "data/strategy-research/reddit-corpus",
            ),
            reddit_corpus_default_corpus_id=_as_str(
                reddit_corpus_section.get("default_corpus_id"),
                "historical-reddit-corpus",
            ),
            reddit_corpus_default_source_id=_as_str(
                reddit_corpus_section.get("source_id"),
                "reddit",
            ),
            reddit_corpus_limit_per_query=max(int(reddit_corpus_section.get("limit_per_query", 50)), 1),
            reddit_corpus_max_pages_per_query=max(int(reddit_corpus_section.get("max_pages_per_query", 5)), 0),
            reddit_corpus_include_comments=_as_bool(reddit_corpus_section.get("include_comments"), False),
            reddit_corpus_comment_limit_per_post=max(int(reddit_corpus_section.get("comment_limit_per_post", 25)), 1),
            reddit_corpus_throttle_sec=max(float(reddit_corpus_section.get("throttle_sec", 0.1)), 0.0),
            x_corpus_base_dir=_as_str(
                x_corpus_section.get("base_dir"),
                "data/strategy-research/x-corpus",
            ),
            x_corpus_default_corpus_id=_as_str(
                x_corpus_section.get("default_corpus_id"),
                "historical-x-corpus",
            ),
            x_corpus_default_source_id=_as_str(
                x_corpus_section.get("source_id"),
                "x",
            ),
            x_corpus_limit_per_query=max(int(x_corpus_section.get("limit_per_query", 50)), 1),
            x_corpus_max_pages_per_query=max(int(x_corpus_section.get("max_pages_per_query", 5)), 0),
            x_corpus_auth_mode=_as_str(x_corpus_section.get("auth_mode"), "bearer").lower(),
            x_corpus_search_endpoint_path=_as_str(
                x_corpus_section.get("search_endpoint_path"),
                "/2/tweets/search/recent",
            ),
            x_corpus_user_lookup_endpoint_path_template=_as_str(
                x_corpus_section.get("user_lookup_endpoint_path_template"),
                "/2/users/by/username/{username}",
            ),
            x_corpus_user_posts_endpoint_path_template=_as_str(
                x_corpus_section.get("user_posts_endpoint_path_template"),
                "/2/users/{user_id}/tweets",
            ),
            x_corpus_throttle_sec=max(float(x_corpus_section.get("throttle_sec", 0.1)), 0.0),
            linkage_base_dir=_as_str(
                linkage_section.get("base_dir"),
                "data/strategy-research/linkage",
            ),
            linkage_default_linkage_id=_as_str(
                linkage_section.get("default_linkage_id"),
                "historical-evidence-linkage",
            ),
            linkage_source_dataset_id=_as_str(
                linkage_section.get("source_dataset_id"),
                _as_str(historical_ingest_section.get("default_dataset_id"), "historical-markets"),
            ),
            linkage_news_corpus_id=_as_str(
                linkage_section.get("news_corpus_id"),
                _as_str(news_corpus_section.get("default_corpus_id"), "historical-news-corpus"),
            ),
            linkage_reddit_corpus_id=_as_str(
                linkage_section.get("reddit_corpus_id"),
                _as_str(reddit_corpus_section.get("default_corpus_id"), "historical-reddit-corpus"),
            ),
            linkage_x_corpus_id=_as_str(
                linkage_section.get("x_corpus_id"),
                _as_str(x_corpus_section.get("default_corpus_id"), "historical-x-corpus"),
            ),
            linkage_enabled_source_classes=_as_str_seq(
                linkage_section.get("enabled_source_classes") or ["news_rss_web", "reddit", "x"]
            ),
            linkage_max_staleness_days=max(int(linkage_section.get("max_staleness_days", 45)), 0),
            linkage_similarity_threshold=max(
                0.0,
                min(float(linkage_section.get("similarity_threshold", 0.20)), 1.0),
            ),
            linkage_ambiguity_margin=max(
                0.0,
                min(float(linkage_section.get("ambiguity_margin", 0.03)), 1.0),
            ),
            linkage_max_candidates_per_evidence=max(int(linkage_section.get("max_candidates_per_evidence", 5)), 1),
            linkage_alias_map=_as_alias_map(linkage_section.get("alias_map")),
            llm_enrichment_base_dir=_as_str(
                llm_enrichment_section.get("base_dir"),
                "data/strategy-research/llm-enrichment",
            ),
            llm_enrichment_default_enrichment_id=_as_str(
                llm_enrichment_section.get("default_enrichment_id"),
                "historical-llm-enrichment",
            ),
            llm_enrichment_default_linkage_id=_as_str(
                llm_enrichment_section.get("linkage_id"),
                _as_str(linkage_section.get("default_linkage_id"), "historical-evidence-linkage"),
            ),
            llm_enrichment_news_corpus_id=_as_str(
                llm_enrichment_section.get("news_corpus_id"),
                _as_str(news_corpus_section.get("default_corpus_id"), "historical-news-corpus"),
            ),
            llm_enrichment_reddit_corpus_id=_as_str(
                llm_enrichment_section.get("reddit_corpus_id"),
                _as_str(reddit_corpus_section.get("default_corpus_id"), "historical-reddit-corpus"),
            ),
            llm_enrichment_x_corpus_id=_as_str(
                llm_enrichment_section.get("x_corpus_id"),
                _as_str(x_corpus_section.get("default_corpus_id"), "historical-x-corpus"),
            ),
            llm_enrichment_enabled=_as_bool(llm_enrichment_section.get("enabled"), False),
            llm_enrichment_provider=_as_str(llm_enrichment_section.get("provider"), "deterministic").lower(),
            llm_enrichment_model_id=_as_str(
                llm_enrichment_section.get("model_id"),
                "deterministic-enrichment-v1",
            ),
            llm_enrichment_prompt_version=_as_str(llm_enrichment_section.get("prompt_version"), "v1"),
            llm_enrichment_enable_fallback=_as_bool(llm_enrichment_section.get("enable_fallback"), True),
            llm_enrichment_allow_external_provider=_as_bool(
                llm_enrichment_section.get("allow_external_provider"),
                False,
            ),
            llm_enrichment_include_states=_as_str_seq(
                llm_enrichment_section.get("include_states") or ["linked", "ambiguous"]
            ),
            llm_enrichment_throttle_sec=max(float(llm_enrichment_section.get("throttle_sec", 0.0)), 0.0),
        )
        ui_users_raw = ui_auth_section.get("users")
        ui_users: list[UiAuthUserSettings] = []
        if isinstance(ui_users_raw, list):
            for row in ui_users_raw:
                if not isinstance(row, Mapping):
                    continue
                username = _as_str(row.get("username"))
                role = _as_str(row.get("role")).lower()
                if not username or role not in {"viewer", "operator", "admin"}:
                    continue
                ui_users.append(
                    UiAuthUserSettings(
                        username=username,
                        role=role,
                        password=_as_str(row.get("password")),
                        password_env=_as_str(row.get("password_env")),
                        password_hash=_as_str(row.get("password_hash")),
                        password_hash_env=_as_str(row.get("password_hash_env")),
                    )
                )
        ui_auth = UiAuthSettings(
            enabled=_as_bool(ui_auth_section.get("enabled"), False),
            session_secret_env=_as_str(
                ui_auth_section.get("session_secret_env"),
                "PM_BOT_UI_SESSION_SECRET",
            ),
            session_timeout_sec=max(int(ui_auth_section.get("session_timeout_sec", 1800)), 1),
            cookie_name=_as_str(ui_auth_section.get("cookie_name"), "pm_bot_ui_session"),
            cookie_secure=_as_bool(ui_auth_section.get("cookie_secure"), True),
            cookie_samesite=_as_str(ui_auth_section.get("cookie_samesite"), "lax").lower(),
            require_password_hashes=_as_bool(ui_auth_section.get("require_password_hashes"), False),
            max_failed_attempts=max(int(ui_auth_section.get("max_failed_attempts", 5)), 1),
            lockout_seconds=max(int(ui_auth_section.get("lockout_seconds", 300)), 1),
            users=tuple(ui_users),
        )
        security = SecuritySettings(
            secrets_from_env=_as_bool(security_section.get("secrets_from_env"), True),
            redact_secrets_in_logs=_as_bool(security_section.get("redact_secrets_in_logs"), True),
            require_explicit_live_flag=_as_bool(security_section.get("require_explicit_live_flag"), True),
            secrets=SecretsSettings(
                backend=_as_str(secrets_section.get("backend"), "env").lower(),
                command_template=_as_str(secrets_section.get("command_template")),
                command_timeout_sec=max(float(secrets_section.get("command_timeout_sec", 5.0)), 0.1),
                env_fallback=_as_bool(secrets_section.get("env_fallback"), True),
            ),
            cli_auth=CliAuthSettings(
                enabled=_as_bool(cli_auth_section.get("enabled"), False),
                actor_user_env=_as_str(cli_auth_section.get("actor_user_env"), "PM_BOT_ACTOR_USER"),
                actor_role_env=_as_str(cli_auth_section.get("actor_role_env"), "PM_BOT_ACTOR_ROLE"),
            ),
            rate_limit=RateLimitSettings(
                enabled=_as_bool(rate_limit_section.get("enabled"), True),
                requests_per_second=max(float(rate_limit_section.get("requests_per_second", 10.0)), 0.1),
                burst=max(int(rate_limit_section.get("burst", 30)), 1),
            ),
        )

        allow_live_execution = _as_bool(feature_flags.get("allow_live_execution"), False)
        return cls(
            runtime=runtime,
            logging=logging_settings,
            metrics=metrics_settings,
            healthcheck=healthcheck_settings,
            performance=performance_settings,
            storage=storage_settings,
            venue=str(venue_section.get("provider", "polymarket")),
            dry_run=_as_bool(venue_section.get("dry_run"), True),
            allow_live_execution=allow_live_execution,
            enable_manual_review_queue=_as_bool(feature_flags.get("enable_manual_review_queue"), False),
            scan=scan,
            prediction=prediction,
            risk=risk,
            execution=execution,
            model_promotion=model_promotion,
            http=http,
            alerting=alerting,
            live_market_data=live_market_data,
            live_research=live_research,
            alt_data=alt_data,
            polymarket_clob=polymarket_clob,
            sandbox_chain=sandbox_chain,
            hyperliquid=hyperliquid,
            strategy_research=strategy_research,
            ui_auth=ui_auth,
            security=security,
        )

    def validate_dry_run_only(self, allow_live_execution: bool | None = None) -> None:
        supported_modes = {
            RuntimeMode.DRY_RUN_STATIC,
            RuntimeMode.PAPER_LIVE,
            RuntimeMode.SANDBOX_CHAIN,
            RuntimeMode.LIVE_DISABLED,
        }
        if self.runtime.mode not in supported_modes:
            raise ValueError("Only dry-run/paper-live/sandbox-chain/live-disabled modes are supported.")
        if not self.dry_run:
            raise ValueError("Live venue execution is disabled in this repository foundation.")
        live_flag = self.allow_live_execution if allow_live_execution is None else bool(allow_live_execution)
        if live_flag:
            raise ValueError("feature_flags.allow_live_execution must be false.")

    def requires_blocking_trade_review(self) -> bool:
        return self.runtime.mode in {RuntimeMode.PAPER_LIVE, RuntimeMode.SANDBOX_CHAIN}

    def effective_blocking_trade_review(self) -> bool:
        return self.execution.blocking_trade_review or self.requires_blocking_trade_review()

    def effective_manual_review_queue(self) -> bool:
        return self.enable_manual_review_queue or self.requires_blocking_trade_review()


def _default_alt_data_capabilities_for_source_class(source_class: str) -> AltDataSourceCapabilitiesSettings:
    normalized = source_class.strip().lower()
    if normalized == "reddit":
        return AltDataSourceCapabilitiesSettings(
            requires_oauth=True,
            requires_user_context=False,
            supports_backfill=True,
            supports_live_polling=True,
            supports_search=True,
            supports_thread_context_expansion=True,
        )
    if normalized == "x":
        return AltDataSourceCapabilitiesSettings(
            requires_oauth=True,
            requires_user_context=False,
            supports_backfill=True,
            supports_live_polling=True,
            supports_search=True,
            supports_thread_context_expansion=True,
        )
    return AltDataSourceCapabilitiesSettings(
        requires_oauth=False,
        requires_user_context=False,
        supports_backfill=True,
        supports_live_polling=True,
        supports_search=True,
        supports_thread_context_expansion=False,
    )


def _parse_alt_data_source(
    *,
    source_id: str,
    payload: Mapping[str, Any],
) -> AltDataSourceSettings:
    source_key = source_id.strip().lower()
    source_class = _as_str(payload.get("source_class"), source_key).lower()
    defaults = _default_alt_data_capabilities_for_source_class(source_class)
    capability_payload = _as_dict(payload.get("capabilities"))
    capabilities = AltDataSourceCapabilitiesSettings(
        requires_oauth=_as_bool(
            capability_payload.get("requires_oauth"),
            defaults.requires_oauth,
        ),
        requires_user_context=_as_bool(
            capability_payload.get("requires_user_context"),
            defaults.requires_user_context,
        ),
        supports_backfill=_as_bool(
            capability_payload.get("supports_backfill"),
            defaults.supports_backfill,
        ),
        supports_live_polling=_as_bool(
            capability_payload.get("supports_live_polling"),
            defaults.supports_live_polling,
        ),
        supports_search=_as_bool(
            capability_payload.get("supports_search"),
            defaults.supports_search,
        ),
        supports_thread_context_expansion=_as_bool(
            capability_payload.get("supports_thread_context_expansion"),
            defaults.supports_thread_context_expansion,
        ),
    )
    return AltDataSourceSettings(
        source_id=source_key,
        source_class=source_class or "news_rss_web",
        enabled=_as_bool(payload.get("enabled"), False),
        adapter=_as_str(payload.get("adapter"), source_key),
        endpoint_url=_as_str(payload.get("endpoint_url")),
        credential_env=_as_str(payload.get("credential_env")),
        capabilities=capabilities,
    )


def _parse_alt_data_sources(
    *,
    alt_data_sources_section: Mapping[str, Any],
    legacy_sources_section: Mapping[str, Any],
) -> tuple[AltDataSourceSettings, ...]:
    sources: list[AltDataSourceSettings] = []
    if alt_data_sources_section:
        for raw_source_id, raw_payload in alt_data_sources_section.items():
            source_id = _as_str(raw_source_id).lower()
            if not source_id:
                continue
            sources.append(_parse_alt_data_source(source_id=source_id, payload=_as_dict(raw_payload)))
        return tuple(sources)

    if not legacy_sources_section:
        return ()

    legacy_to_class = {
        "twitter": "x",
        "x": "x",
        "reddit": "reddit",
        "rss": "news_rss_web",
        "official": "news_rss_web",
    }
    for raw_source_id, raw_payload in legacy_sources_section.items():
        source_id = _as_str(raw_source_id).lower()
        if not source_id:
            continue
        source_payload = _as_dict(raw_payload)
        source_class = legacy_to_class.get(source_id, source_id)
        defaults = _default_alt_data_capabilities_for_source_class(source_class)
        sources.append(
            AltDataSourceSettings(
                source_id=source_id,
                source_class=source_class,
                enabled=_as_bool(source_payload.get("enabled"), False),
                adapter=f"legacy_{source_id}",
                endpoint_url="",
                credential_env="",
                capabilities=defaults,
            )
        )
    return tuple(sources)
