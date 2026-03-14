from __future__ import annotations

from dataclasses import dataclass
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
class StorageSettings:
    operational_db_path: str = "data/runtime.db"
    artifacts_dir: str = "data/artifacts"
    audit_log_path: str = "data/audit/events.jsonl"


@dataclass(slots=True, frozen=True)
class ScanSettings:
    min_liquidity_usd: float = 10_000.0
    min_volume_24h_usd: float = 5_000.0
    min_hours_to_resolution: float = 6.0
    max_spread_bps: int = 300
    anomaly_move_bps: int = 150


@dataclass(slots=True, frozen=True)
class PredictionSettings:
    min_confidence: float = 0.62
    min_edge_bps: int = 300
    market_weight: float = 0.45
    narrative_weight: float = 0.45
    structure_weight: float = 0.10


@dataclass(slots=True, frozen=True)
class RiskSettings:
    bankroll_usd: float = 10_000.0
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

    @property
    def user_agent_with_contact(self) -> str:
        contact = self.contact.strip()
        if not contact:
            return self.user_agent
        return f"{self.user_agent} ({contact})"


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
class ExecutionSettings:
    mode: ExecutionMode = ExecutionMode.PAPER
    rehearsal_mode: ExecutionMode = ExecutionMode.SANDBOX_CHAIN
    enable_rehearsal_lane: bool = True
    blocking_trade_review: bool = True
    review_auto_approve: bool = False
    review_candidate_expiry_seconds: int = 0
    settlement_same_run: bool = False


@dataclass(slots=True, frozen=True)
class UiAuthUserSettings:
    username: str
    role: str
    password: str = ""
    password_env: str = ""


@dataclass(slots=True, frozen=True)
class UiAuthSettings:
    enabled: bool = False
    session_secret_env: str = "PM_BOT_UI_SESSION_SECRET"
    session_timeout_sec: int = 1800
    cookie_name: str = "pm_bot_ui_session"
    cookie_secure: bool = True
    cookie_samesite: str = "lax"
    users: tuple[UiAuthUserSettings, ...] = ()


@dataclass(slots=True, frozen=True)
class AppSettings:
    runtime: RuntimeSettings
    logging: LoggingSettings
    metrics: MetricsSettings
    healthcheck: HealthcheckSettings
    storage: StorageSettings
    venue: str
    dry_run: bool
    allow_live_execution: bool
    enable_manual_review_queue: bool
    scan: ScanSettings
    prediction: PredictionSettings
    risk: RiskSettings
    execution: ExecutionSettings
    http: HttpSettings
    live_market_data: LiveMarketDataSettings
    live_research: LiveResearchSettings
    sandbox_chain: SandboxChainSettings
    ui_auth: UiAuthSettings

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
        logs_section = _as_dict(observability.get("logs"))
        storage_section = _as_dict(app_config.get("storage"))
        audit_log_section = _as_dict(storage_section.get("audit_log"))
        http_section = _as_dict(app_config.get("http"))
        live_market_section = _as_dict(app_config.get("live_market_data"))
        live_research_section = _as_dict(app_config.get("live_research"))
        wikipedia_research_section = _as_dict(live_research_section.get("wikipedia"))
        openalex_research_section = _as_dict(live_research_section.get("openalex"))
        sandbox_chain_section = _as_dict(app_config.get("sandbox_chain"))
        ui_auth_section = _as_dict(app_config.get("ui_auth"))

        thresholds = _as_dict(agents_config.get("thresholds"))
        risk_section = _as_dict(agents_config.get("risk"))
        prediction_agent = _as_dict(_as_dict(agents_config.get("agents")).get("prediction"))
        components = _as_dict(prediction_agent.get("components"))

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
        storage_settings = StorageSettings(
            operational_db_path=str(_as_dict(storage_section.get("operational_db")).get("path", "data/runtime.db")),
            artifacts_dir=str(storage_section.get("artifacts_dir", "data/artifacts")),
            audit_log_path=str(audit_log_section.get("path", "data/audit/events.jsonl")),
        )
        scan = ScanSettings(
            min_liquidity_usd=float(thresholds.get("min_liquidity_usd", 10_000.0)),
            min_volume_24h_usd=float(thresholds.get("min_volume_24h_usd", 5_000.0)),
            min_hours_to_resolution=float(thresholds.get("min_hours_to_resolution", 6.0)),
            max_spread_bps=int(thresholds.get("max_spread_bps", 300)),
        )
        prediction = PredictionSettings(
            min_confidence=float(thresholds.get("min_confidence", 0.62)),
            min_edge_bps=int(thresholds.get("min_edge_bps", 300)),
            market_weight=float(components.get("market_weight", 0.45)),
            narrative_weight=float(components.get("narrative_weight", 0.45)),
            structure_weight=float(components.get("structure_weight", 0.10)),
        )
        risk = RiskSettings(
            bankroll_usd=float(risk_section.get("bankroll_usd", 10_000.0)),
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
            users=tuple(ui_users),
        )

        allow_live_execution = _as_bool(feature_flags.get("allow_live_execution"), False)
        return cls(
            runtime=runtime,
            logging=logging_settings,
            metrics=metrics_settings,
            healthcheck=healthcheck_settings,
            storage=storage_settings,
            venue=str(venue_section.get("provider", "polymarket")),
            dry_run=_as_bool(venue_section.get("dry_run"), True),
            allow_live_execution=allow_live_execution,
            enable_manual_review_queue=_as_bool(feature_flags.get("enable_manual_review_queue"), False),
            scan=scan,
            prediction=prediction,
            risk=risk,
            execution=execution,
            http=http,
            live_market_data=live_market_data,
            live_research=live_research,
            sandbox_chain=sandbox_chain,
            ui_auth=ui_auth,
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
