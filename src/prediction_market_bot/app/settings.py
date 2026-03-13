from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


@dataclass(slots=True, frozen=True)
class RuntimeSettings:
    app_name: str = "prediction-market-bot"
    env: str = "development"
    timezone: str = "UTC"
    mode: str = "dry-run"


@dataclass(slots=True, frozen=True)
class LoggingSettings:
    level: str = "INFO"
    json_logs: bool = True


@dataclass(slots=True, frozen=True)
class StorageSettings:
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
class AppSettings:
    runtime: RuntimeSettings
    logging: LoggingSettings
    storage: StorageSettings
    venue: str
    dry_run: bool
    enable_manual_review_queue: bool
    scan: ScanSettings
    prediction: PredictionSettings
    risk: RiskSettings

    @classmethod
    def from_dicts(cls, app_config: Mapping[str, Any], agents_config: Mapping[str, Any]) -> "AppSettings":
        app_section = _as_dict(app_config.get("app"))
        venue_section = _as_dict(app_config.get("venue"))
        feature_flags = _as_dict(app_config.get("feature_flags"))
        observability = _as_dict(app_config.get("observability"))
        storage_section = _as_dict(app_config.get("storage"))
        audit_log_section = _as_dict(storage_section.get("audit_log"))
        thresholds = _as_dict(agents_config.get("thresholds"))
        risk_section = _as_dict(agents_config.get("risk"))
        prediction_agent = _as_dict(_as_dict(agents_config.get("agents")).get("prediction"))
        components = _as_dict(prediction_agent.get("components"))

        runtime = RuntimeSettings(
            app_name=str(app_section.get("name", "prediction-market-bot")),
            env=str(app_section.get("env", "development")),
            timezone=str(app_section.get("timezone", "UTC")),
            mode=str(app_section.get("mode", "dry-run")),
        )
        logging_settings = LoggingSettings(
            level=str(observability.get("log_level", "INFO")),
            json_logs=bool(observability.get("json_logs", True)),
        )
        storage_settings = StorageSettings(
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
            global_circuit_breaker=bool(risk_section.get("global_circuit_breaker", False)),
            manual_pause=bool(risk_section.get("manual_pause", False)),
            min_bet_usd=float(risk_section.get("min_bet_usd", 25.0)),
        )
        return cls(
            runtime=runtime,
            logging=logging_settings,
            storage=storage_settings,
            venue=str(venue_section.get("provider", "polymarket")),
            dry_run=bool(venue_section.get("dry_run", True)),
            enable_manual_review_queue=bool(feature_flags.get("enable_manual_review_queue", False)),
            scan=scan,
            prediction=prediction,
            risk=risk,
        )

    def validate_dry_run_only(self, allow_live_execution: bool) -> None:
        if self.runtime.mode.lower() != "dry-run":
            raise ValueError("Only dry-run mode is supported.")
        if not self.dry_run:
            raise ValueError("Live venue execution is disabled in this repository foundation.")
        if allow_live_execution:
            raise ValueError("feature_flags.allow_live_execution must be false.")
