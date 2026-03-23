from __future__ import annotations

import logging
from dataclasses import replace
from typing import Mapping, Sequence

from prediction_market_bot.agents.execution import (
    ExecutionAgent,
    LiveDisabledExecutor,
    PaperExecutor,
    ShadowSignExecutor,
)
from prediction_market_bot.agents.postmortem import PostmortemAgent
from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.agents.research import ResearchAgent
from prediction_market_bot.agents.risk import RiskAgent
from prediction_market_bot.agents.scanner import ScanAgent
from prediction_market_bot.agents.settlement import SettlementAgent
from prediction_market_bot.app.settings import AppSettings
from prediction_market_bot.app.secrets import SecretProvider, build_secret_provider, resolve_operational_db_dsn
from prediction_market_bot.domain.enums import (
    ExecutionMode,
    ProviderFailurePolicy,
    ProviderSelection,
    RuntimeMode,
    TradeReviewAction,
    TradeReviewStatus,
)
from prediction_market_bot.domain.models import (
    MarketCandidate,
    MarketSnapshot,
    PredictionResult,
    ResearchFinding,
    RiskDecision,
    TradeReviewCandidate,
)
from prediction_market_bot.infrastructure import (
    JsonlPersistence,
    LiveResearchIngestionPipeline,
    OperationalRepositories,
    PostgresOperationalRepositories,
    PolymarketReadOnlyMarketDataAdapter,
    SandboxChainExecutor,
    SqliteOperationalRepositories,
    StaticMarketDataProvider,
    StructuredHttpClient,
    build_default_research_sources,
    build_live_research_sources,
)
from prediction_market_bot.interfaces import MarketDataPort, ResearchDataPort, TradeExecutor
from prediction_market_bot.orchestration import PipelineCoordinator
from prediction_market_bot.services import PaperPortfolioEngine, SettlementRequestQueueService, TradeReviewQueueService
from prediction_market_bot.services import AlertEvent, build_alerting_service
from prediction_market_bot.services.model_promotion import resolve_runtime_model_gate_decision
from prediction_market_bot.services.operator_control import load_operator_state, operator_state_path

logger = logging.getLogger(__name__)


class _StaticResearchCompositeSource(ResearchDataPort):
    def __init__(self, sources: Sequence[ResearchDataPort]) -> None:
        self._sources = tuple(sources)

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        findings: list[ResearchFinding] = []
        for source in self._sources:
            findings.extend(source.fetch(market))
        return findings


class _FallbackMarketDataProvider(MarketDataPort):
    def __init__(self, primary: MarketDataPort, fallback: MarketDataPort) -> None:
        self.primary = primary
        self.fallback = fallback

    def list_active_markets(self) -> Sequence[MarketSnapshot]:
        try:
            return self.primary.list_active_markets()
        except Exception as exc:
            logger.error(
                "market_data_provider_fallback",
                extra={
                    "event": "market_data_provider_fallback",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return self.fallback.list_active_markets()


class _FallbackResearchSource(ResearchDataPort):
    def __init__(self, primary: ResearchDataPort, fallback: ResearchDataPort) -> None:
        self.primary = primary
        self.fallback = fallback

    def fetch(self, market: MarketSnapshot) -> Sequence[ResearchFinding]:
        try:
            return self.primary.fetch(market)
        except Exception as exc:
            logger.error(
                "research_provider_fallback",
                extra={
                    "event": "research_provider_fallback",
                    "market_id": market.market_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return self.fallback.fetch(market)


def build_persistence(settings: AppSettings) -> JsonlPersistence:
    return JsonlPersistence(
        artifacts_dir=settings.storage.artifacts_dir,
        audit_log_path=settings.storage.audit_log_path,
    )


def build_operational_repositories(settings: AppSettings) -> OperationalRepositories:
    driver = settings.storage.operational_db_driver.strip().lower()
    if driver == "sqlite":
        return SqliteOperationalRepositories.bootstrap(
            settings.storage.operational_db_path,
            apply_migrations=settings.storage.operational_db_auto_migrate_on_boot,
            dialect="sqlite",
        )
    if driver == "postgres":
        try:
            dsn = resolve_operational_db_dsn(settings)
        except Exception as exc:
            raise ValueError(f"Invalid operational_db secrets config: {exc}") from exc
        if not dsn:
            raise ValueError(
                "Invalid operational_db config for postgres: "
                "storage.operational_db.dsn is required (or set dsn_env)."
            )
        return PostgresOperationalRepositories.bootstrap(
            dsn,
            apply_migrations=settings.storage.operational_db_auto_migrate_on_boot,
            dialect="postgres",
        )
    raise ValueError(
        "Unsupported operational_db driver. "
        "Configured driver='{driver}'. Supported: sqlite, postgres."
        .format(driver=driver or "unset")
    )


def build_http_client(
    settings: AppSettings,
    *,
    secrets: SecretProvider | None = None,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
    retry_backoff_sec: float | None = None,
    retry_jitter_sec: float | None = None,
    cache_ttl_sec: int | None = None,
) -> StructuredHttpClient:
    secret_provider = secrets if secrets is not None else build_secret_provider(settings)
    openalex_api_key = secret_provider.get(settings.http.openalex_api_key_env)
    return StructuredHttpClient(
        user_agent=settings.http.user_agent,
        contact=settings.http.contact,
        timeout_sec=timeout_sec if timeout_sec is not None else settings.http.timeout_sec,
        max_retries=max_retries if max_retries is not None else settings.http.max_retries,
        retry_backoff_sec=retry_backoff_sec if retry_backoff_sec is not None else settings.http.retry_backoff_sec,
        retry_jitter_sec=retry_jitter_sec if retry_jitter_sec is not None else settings.http.retry_jitter_sec,
        cache_ttl_sec=cache_ttl_sec if cache_ttl_sec is not None else settings.http.cache_ttl_sec,
        openalex_api_key=openalex_api_key,
        enforce_allowed_hosts=settings.http.enforce_allowed_hosts,
        allowed_hosts=settings.http.allowed_hosts,
        max_response_bytes=settings.http.max_response_bytes,
    )


def _resolve_provider_selection(selection: ProviderSelection, runtime_mode: RuntimeMode) -> ProviderSelection:
    if selection != ProviderSelection.AUTO:
        return selection
    if runtime_mode in {RuntimeMode.PAPER_LIVE, RuntimeMode.SANDBOX_CHAIN}:
        return ProviderSelection.LIVE
    return ProviderSelection.STATIC


def build_live_market_data_provider(
    settings: AppSettings,
    persistence: JsonlPersistence,
    http_client: StructuredHttpClient,
    *,
    secrets: SecretProvider | None = None,
    endpoint_url: str | None = None,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
    retry_backoff_sec: float | None = None,
    retry_jitter_sec: float | None = None,
    max_staleness_seconds: int | None = None,
    default_limit: int | None = None,
) -> PolymarketReadOnlyMarketDataAdapter:
    secret_provider = secrets if secrets is not None else build_secret_provider(settings)
    api_key = ""
    api_key_env = settings.live_market_data.api_key_env.strip()
    if api_key_env:
        api_key = secret_provider.get(api_key_env)
    return PolymarketReadOnlyMarketDataAdapter(
        endpoint_url=endpoint_url if endpoint_url is not None else settings.live_market_data.endpoint_url,
        timeout_sec=timeout_sec if timeout_sec is not None else settings.live_market_data.timeout_sec,
        max_retries=max_retries if max_retries is not None else settings.live_market_data.max_retries,
        retry_backoff_sec=retry_backoff_sec if retry_backoff_sec is not None else settings.live_market_data.retry_backoff_sec,
        retry_jitter_sec=retry_jitter_sec if retry_jitter_sec is not None else settings.live_market_data.retry_jitter_sec,
        max_staleness_seconds=(
            max_staleness_seconds if max_staleness_seconds is not None else settings.live_market_data.max_staleness_sec
        ),
        default_limit=default_limit if default_limit is not None else settings.live_market_data.limit,
        cache_ttl_sec=settings.live_market_data.cache_ttl_sec,
        headers=settings.live_market_data.headers or {},
        api_key=api_key,
        api_key_header=settings.live_market_data.api_key_header,
        api_key_prefix=settings.live_market_data.api_key_prefix,
        require_api_key=settings.live_market_data.require_api_key,
        persistence=persistence,
        http_client=http_client,
    )


def build_market_data_provider(
    settings: AppSettings,
    persistence: JsonlPersistence,
    http_client: StructuredHttpClient,
    *,
    secrets: SecretProvider | None = None,
    provider_selection: ProviderSelection | None = None,
    failure_policy: ProviderFailurePolicy | None = None,
) -> MarketDataPort:
    selection = _resolve_provider_selection(
        provider_selection if provider_selection is not None else settings.runtime.market_data_provider,
        settings.runtime.mode,
    )
    policy = failure_policy if failure_policy is not None else settings.runtime.provider_failure_policy

    if selection == ProviderSelection.STATIC:
        return StaticMarketDataProvider(venue=settings.venue)

    live_provider = build_live_market_data_provider(
        settings,
        persistence,
        http_client,
        secrets=secrets,
    )
    if policy == ProviderFailurePolicy.FALLBACK_TO_STATIC:
        logger.warning(
            "market_data_provider_fallback_enabled",
            extra={
                "event": "market_data_provider_fallback_enabled",
                "runtime_mode": settings.runtime.mode.value,
            },
        )
        return _FallbackMarketDataProvider(live_provider, StaticMarketDataProvider(venue=settings.venue))
    return live_provider


def build_live_research_pipeline(
    settings: AppSettings,
    persistence: JsonlPersistence,
    http_client: StructuredHttpClient,
    *,
    secrets: SecretProvider | None = None,
    wikipedia_endpoint_url: str | None = None,
    openalex_endpoint_url: str | None = None,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
    retry_backoff_sec: float | None = None,
    retry_jitter_sec: float | None = None,
    cache_ttl_seconds: int | None = None,
    default_limit_per_source: int | None = None,
) -> LiveResearchIngestionPipeline:
    secret_provider = secrets if secrets is not None else build_secret_provider(settings)
    wikipedia_api_key = ""
    if settings.live_research.wikipedia_api_key_env.strip():
        wikipedia_api_key = secret_provider.get(settings.live_research.wikipedia_api_key_env.strip())
    openalex_api_key = ""
    if settings.live_research.openalex_api_key_env.strip():
        openalex_api_key = secret_provider.get(settings.live_research.openalex_api_key_env.strip())
    live_sources = build_live_research_sources(
        wikipedia_endpoint_url=(
            wikipedia_endpoint_url if wikipedia_endpoint_url is not None else settings.live_research.wikipedia_endpoint_url
        ),
        openalex_endpoint_url=(
            openalex_endpoint_url if openalex_endpoint_url is not None else settings.live_research.openalex_endpoint_url
        ),
        timeout_sec=timeout_sec if timeout_sec is not None else settings.http.timeout_sec,
        max_retries=max_retries if max_retries is not None else settings.http.max_retries,
        retry_backoff_sec=retry_backoff_sec if retry_backoff_sec is not None else settings.http.retry_backoff_sec,
        retry_jitter_sec=retry_jitter_sec if retry_jitter_sec is not None else settings.http.retry_jitter_sec,
        cache_ttl_seconds=cache_ttl_seconds if cache_ttl_seconds is not None else settings.http.cache_ttl_sec,
        wikipedia_timeout_sec=(
            timeout_sec if timeout_sec is not None else settings.live_research.wikipedia_timeout_sec
        ),
        wikipedia_max_retries=(
            max_retries if max_retries is not None else settings.live_research.wikipedia_max_retries
        ),
        wikipedia_retry_backoff_sec=(
            retry_backoff_sec if retry_backoff_sec is not None else settings.live_research.wikipedia_retry_backoff_sec
        ),
        wikipedia_retry_jitter_sec=(
            retry_jitter_sec if retry_jitter_sec is not None else settings.live_research.wikipedia_retry_jitter_sec
        ),
        wikipedia_cache_ttl_seconds=(
            cache_ttl_seconds if cache_ttl_seconds is not None else settings.live_research.wikipedia_cache_ttl_sec
        ),
        wikipedia_headers=settings.live_research.wikipedia_headers or {},
        wikipedia_api_key=wikipedia_api_key,
        wikipedia_api_key_header=settings.live_research.wikipedia_api_key_header,
        wikipedia_api_key_prefix=settings.live_research.wikipedia_api_key_prefix,
        wikipedia_require_api_key=settings.live_research.wikipedia_require_api_key,
        openalex_timeout_sec=(
            timeout_sec if timeout_sec is not None else settings.live_research.openalex_timeout_sec
        ),
        openalex_max_retries=(
            max_retries if max_retries is not None else settings.live_research.openalex_max_retries
        ),
        openalex_retry_backoff_sec=(
            retry_backoff_sec if retry_backoff_sec is not None else settings.live_research.openalex_retry_backoff_sec
        ),
        openalex_retry_jitter_sec=(
            retry_jitter_sec if retry_jitter_sec is not None else settings.live_research.openalex_retry_jitter_sec
        ),
        openalex_cache_ttl_seconds=(
            cache_ttl_seconds if cache_ttl_seconds is not None else settings.live_research.openalex_cache_ttl_sec
        ),
        openalex_headers=settings.live_research.openalex_headers or {},
        openalex_api_key=openalex_api_key,
        openalex_api_key_header=settings.live_research.openalex_api_key_header,
        openalex_api_key_prefix=settings.live_research.openalex_api_key_prefix,
        openalex_require_api_key=settings.live_research.openalex_require_api_key,
        http_client=http_client,
    )
    return LiveResearchIngestionPipeline(
        sources=live_sources,
        persistence=persistence,
        default_limit_per_source=(
            default_limit_per_source if default_limit_per_source is not None else settings.live_research.limit_per_source
        ),
    )


def build_research_sources(
    settings: AppSettings,
    persistence: JsonlPersistence,
    http_client: StructuredHttpClient,
    *,
    secrets: SecretProvider | None = None,
    provider_selection: ProviderSelection | None = None,
    failure_policy: ProviderFailurePolicy | None = None,
) -> list[ResearchDataPort]:
    selection = _resolve_provider_selection(
        provider_selection if provider_selection is not None else settings.runtime.research_provider,
        settings.runtime.mode,
    )
    policy = failure_policy if failure_policy is not None else settings.runtime.provider_failure_policy

    if selection == ProviderSelection.STATIC:
        return list(build_default_research_sources())

    live_pipeline = build_live_research_pipeline(
        settings,
        persistence,
        http_client,
        secrets=secrets,
    )
    if policy == ProviderFailurePolicy.FALLBACK_TO_STATIC:
        logger.warning(
            "research_provider_fallback_enabled",
            extra={
                "event": "research_provider_fallback_enabled",
                "runtime_mode": settings.runtime.mode.value,
            },
        )
        static_sources = list(build_default_research_sources())
        fallback_source = _StaticResearchCompositeSource(static_sources)
        return [_FallbackResearchSource(live_pipeline, fallback_source)]
    return [live_pipeline]


def _build_executor_for_mode(
    *,
    mode: ExecutionMode,
    settings: AppSettings,
    http_client: StructuredHttpClient,
    secrets: SecretProvider | None = None,
) -> TradeExecutor:
    secret_provider = secrets if secrets is not None else build_secret_provider(settings)
    if mode == ExecutionMode.PAPER:
        return PaperExecutor()
    if mode == ExecutionMode.SHADOW_SIGN:
        return ShadowSignExecutor()
    if mode == ExecutionMode.SANDBOX_CHAIN:
        private_key = ""
        private_key_env = settings.sandbox_chain.private_key_env.strip()
        if private_key_env:
            private_key = secret_provider.get(private_key_env)
        return SandboxChainExecutor(
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
    return LiveDisabledExecutor()


def _build_critical_alert_hook(settings: AppSettings):
    alerting = build_alerting_service(settings)

    def _critical_alert_hook(event_type: str, payload: Mapping[str, object]) -> None:
        logger.critical(
            "critical_failure_alert",
            extra={
                "event": "critical_failure_alert",
                "alert_event_type": event_type,
                **dict(payload),
            },
        )
        alerting.emit(
            AlertEvent(
                event_type=event_type,
                severity="critical",
                title="Pipeline critical failure",
                message=f"critical runtime failure detected: event_type={event_type}",
                details=dict(payload),
                dedupe_key=f"critical:{event_type}",
            )
        )

    return _critical_alert_hook


def _resolve_alt_shadow_promoted_activation(
    *,
    settings: AppSettings,
    model_gate_allowed: bool,
    model_gate_effective_engine: str,
) -> tuple[bool, str]:
    if not settings.prediction.alt_shadow_enabled:
        return False, "alt_shadow_disabled"
    if not settings.prediction.alt_shadow_promoted_enabled:
        return False, "alt_shadow_promoted_disabled"
    if settings.runtime.mode != RuntimeMode.SANDBOX_CHAIN:
        return False, "alt_shadow_promoted_requires_sandbox_chain"
    allowed_modes = {
        item.strip().upper()
        for item in settings.prediction.alt_shadow_promoted_runtime_modes
        if item.strip()
    }
    if allowed_modes and settings.runtime.mode.value not in allowed_modes:
        return False, "alt_shadow_promoted_runtime_mode_not_allowed"
    if not model_gate_allowed or model_gate_effective_engine != "model_v2":
        return False, "model_v2_gate_not_satisfied_for_alt_shadow_promotion"
    return True, "alt_shadow_promoted_allowed"


def build_coordinator(settings: AppSettings, persistence: JsonlPersistence) -> PipelineCoordinator:
    secrets = build_secret_provider(settings)
    operational = build_operational_repositories(settings)
    state = load_operator_state(
        operator_state_path(settings.storage.artifacts_dir),
        repository=operational.operator_control_state,
    )
    model_gate_decision = resolve_runtime_model_gate_decision(settings=settings, state=state)
    if not model_gate_decision.allowed and model_gate_decision.effective_engine != "heuristic":
        raise ValueError(
            "prediction_model_gate_blocked "
            f"reason={model_gate_decision.reason} requested_engine={model_gate_decision.requested_engine} "
            "Set prediction.model_inference.fallback_to_heuristic=true or promote model_v2 explicitly."
        )
    forced_heuristic_reason = settings.prediction.forced_heuristic_reason
    if model_gate_decision.effective_engine == "heuristic" and model_gate_decision.reason not in {
        "",
        "prediction_engine_not_model_v2",
        "model_v2_allowed",
    }:
        forced_heuristic_reason = model_gate_decision.reason
    prediction_engine = model_gate_decision.effective_engine
    alt_shadow_promoted_active, alt_shadow_promoted_reason = _resolve_alt_shadow_promoted_activation(
        settings=settings,
        model_gate_allowed=model_gate_decision.allowed,
        model_gate_effective_engine=model_gate_decision.effective_engine,
    )
    if alt_shadow_promoted_active:
        prediction_engine = "model_v2_alt_promoted"
    elif settings.prediction.alt_shadow_promoted_enabled:
        logger.warning(
            "alt_shadow_promotion_not_activated",
            extra={
                "event": "alt_shadow_promotion_not_activated",
                "runtime_mode": settings.runtime.mode.value,
                "reason": alt_shadow_promoted_reason,
            },
        )
    prediction_settings = replace(
        settings.prediction,
        engine=prediction_engine,
        forced_heuristic_reason=forced_heuristic_reason,
    )
    if model_gate_decision.requested_engine != model_gate_decision.effective_engine:
        logger.warning(
            "prediction_model_gate_engine_override",
            extra={
                "event": "prediction_model_gate_engine_override",
                **model_gate_decision.to_dict(),
            },
        )
    else:
        logger.info(
            "prediction_model_gate_decision",
            extra={
                "event": "prediction_model_gate_decision",
                **model_gate_decision.to_dict(),
            },
        )
    http_client = build_http_client(settings, secrets=secrets)
    market_data = build_market_data_provider(settings, persistence, http_client, secrets=secrets)
    research_sources = build_research_sources(settings, persistence, http_client, secrets=secrets)
    research_agent = ResearchAgent(research_sources)
    logger.info(
        "runtime_provider_selection",
        extra={
            "event": "runtime_provider_selection",
            "runtime_mode": settings.runtime.mode.value,
            "market_data_provider": settings.runtime.market_data_provider.value,
            "research_provider": settings.runtime.research_provider.value,
            "provider_failure_policy": settings.runtime.provider_failure_policy.value,
            "execution_mode": settings.execution.mode.value,
            "prediction_requested_engine": model_gate_decision.requested_engine,
            "prediction_effective_engine": model_gate_decision.effective_engine,
            "prediction_gate_reason": model_gate_decision.reason,
            "alt_shadow_promoted_active": alt_shadow_promoted_active,
            "alt_shadow_promoted_reason": alt_shadow_promoted_reason,
        },
    )

    main_executor = _build_executor_for_mode(
        mode=settings.execution.mode,
        settings=settings,
        http_client=http_client,
        secrets=secrets,
    )
    rehearsal_executor = None
    rehearsal_mode: ExecutionMode | None = None
    if settings.execution.enable_rehearsal_lane:
        rehearsal_mode = settings.execution.rehearsal_mode
        rehearsal_executor = _build_executor_for_mode(
            mode=rehearsal_mode,
            settings=settings,
            http_client=http_client,
            secrets=secrets,
        )

    review_queue_enabled = settings.effective_manual_review_queue() or settings.execution.review_auto_approve
    review_blocking_gate = settings.effective_blocking_trade_review()
    if settings.requires_blocking_trade_review() and not settings.execution.blocking_trade_review:
        logger.warning(
            "blocking_trade_review_forced_by_runtime_mode",
            extra={
                "event": "blocking_trade_review_forced_by_runtime_mode",
                "runtime_mode": settings.runtime.mode.value,
            },
        )
    if settings.requires_blocking_trade_review() and not settings.enable_manual_review_queue:
        logger.warning(
            "manual_review_queue_forced_by_runtime_mode",
            extra={
                "event": "manual_review_queue_forced_by_runtime_mode",
                "runtime_mode": settings.runtime.mode.value,
            },
        )
    review_queue = (
        TradeReviewQueueService(
            persistence,
            candidate_repo=operational.review_queue,
            decision_repo=operational.review_decisions,
        )
        if review_queue_enabled
        else None
    )
    review_queue_hook = None
    if review_queue is not None:

        def _enqueue_review_candidate(
            run_id: str,
            candidate: MarketCandidate,
            prediction: PredictionResult,
            risk: RiskDecision,
        ) -> TradeReviewCandidate:
            queued = review_queue.enqueue_candidate(
                run_id,
                candidate,
                prediction,
                risk,
                expires_in_seconds=settings.execution.review_candidate_expiry_seconds,
            )
            if settings.execution.review_auto_approve:
                current = review_queue.get_item(queued.queue_id)
                if current is not None and current.status in {
                    TradeReviewStatus.PENDING_REVIEW,
                    TradeReviewStatus.PENDING,
                }:
                    review_queue.apply_action(
                        queue_id=queued.queue_id,
                        action=TradeReviewAction.APPROVE,
                        operator_id="system-auto-review",
                        operator_rationale="auto_approved_via_explicit_config",
                        note="Auto-approved by execution.review_auto_approve.",
                    )
            return queued

        review_queue_hook = _enqueue_review_candidate

    review_gate_hook = None
    if review_queue is not None and review_blocking_gate:

        def _review_gate(candidate: TradeReviewCandidate) -> tuple[bool, str]:
            queue_id = candidate.queue_id.strip()
            if not queue_id:
                return False, "manual_review_queue_id_missing"
            item = review_queue.get_item(queue_id)
            if item is None:
                return False, "manual_review_pending"
            if item.status == TradeReviewStatus.APPROVED:
                return True, "manual_review_approved"
            if item.status == TradeReviewStatus.REJECTED:
                return False, "manual_review_rejected"
            if item.status == TradeReviewStatus.EXPIRED:
                return False, "manual_review_expired"
            return False, "manual_review_pending"

        review_gate_hook = _review_gate

    portfolio = PaperPortfolioEngine(
        persistence=persistence,
        open_positions_repo=operational.open_positions,
    )
    restored = portfolio.restore_from_repository()
    if not restored:
        portfolio.replay_rows(persistence.read_all_artifact_records("paper_portfolio_events"))
    settlement_queue = SettlementRequestQueueService(
        persistence,
        pending_repo=operational.pending_settlements,
    )

    return PipelineCoordinator(
        market_data=market_data,
        scanner=ScanAgent(settings.scan),
        research=research_agent,
        prediction=PredictionAgent(prediction_settings),
        risk=RiskAgent(settings.risk, prediction_settings),
        execution=ExecutionAgent(
            settings.venue,
            main_executor,
            execution_mode=settings.execution.mode,
            rehearsal_executor=rehearsal_executor,
            rehearsal_mode=rehearsal_mode,
        ),
        settlement=SettlementAgent(),
        postmortem=PostmortemAgent(),
        persistence=persistence,
        portfolio=portfolio,
        alert_hook=_build_critical_alert_hook(settings),
        review_queue_hook=review_queue_hook,
        review_gate_hook=review_gate_hook,
        settlement_request_hook=settlement_queue.enqueue_from_execution,
        run_repo=operational.runs,
        transaction_intent_repo=operational.transaction_intents,
        transaction_attempt_repo=operational.transaction_attempts,
        transaction_receipt_repo=operational.transaction_receipts,
        review_blocking_gate=review_blocking_gate,
        settlement_same_run=settings.execution.settlement_same_run,
        slow_stage_threshold_ms=settings.performance.slow_stage_threshold_ms,
    )
