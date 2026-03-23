from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.ui.models import (
    ChartPointResponse,
    IncidentBannerResponse,
    RiskRowResponse,
    RiskTabResponse,
    RunSelectorResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import chart_from_counter, empty_risk, to_float, to_text, to_text_tuple


def build_risk_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> RiskTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_risk(selector, "no_run_selected")
    decisions = queries.artifact_payloads(run, "effective_risk_decisions")
    if not decisions:
        decisions = queries.artifact_payloads(run, "risk_decisions")
    rows: list[RiskRowResponse] = []
    reason_counter: dict[str, int] = {}
    guardrail_counter: dict[str, int] = {}
    approved_count = 0
    stake_sum = 0.0
    proposed_stake_sum = 0.0
    portfolio_exposure_sum = 0.0
    market_exposure_sum = 0.0
    exposure_rows = 0
    circuit_breaker_active = False
    daily_stop_triggered = False
    bankroll_fraction_sum = 0.0
    fractional_kelly_sum = 0.0
    bankroll_usd = max(context.settings.risk.bankroll_usd, 0.0)
    for payload in decisions:
        approved = bool(payload.get("approved", False))
        reasoning = to_text_tuple(payload.get("reasoning"))
        if approved:
            approved_count += 1
        else:
            for reason in reasoning:
                token = reason.split(" ", 1)[0].strip()
                if token and token != "approved":
                    reason_counter[token] = reason_counter.get(token, 0) + 1
        for reason in reasoning:
            token = reason.split(" ", 1)[0].strip().lower()
            if not token:
                continue
            if token.startswith("daily_stop_triggered"):
                daily_stop_triggered = True
            if token in {"global_circuit_breaker_active", "global_circuit_breaker_trip_daily_stop"}:
                circuit_breaker_active = True
            if "_cap" in token or "guardrail" in token or token.endswith("_triggered") or token.endswith("_active"):
                guardrail_counter[token] = guardrail_counter.get(token, 0) + 1
            if token.startswith("global_circuit_breaker="):
                circuit_breaker_active = circuit_breaker_active or token.endswith("=true")

        final_fraction = _extract_reasoning_float(reasoning, "final_fraction")
        if final_fraction is not None and bankroll_usd > 0:
            proposed_stake_sum += max(final_fraction, 0.0) * bankroll_usd
        else:
            proposed_stake_sum += to_float(payload.get("stake_usd"))
        portfolio_exposure = _extract_reasoning_float(reasoning, "portfolio_exposure_usd")
        market_exposure = _extract_reasoning_float(reasoning, "market_exposure_usd")
        if portfolio_exposure is not None:
            portfolio_exposure_sum += max(portfolio_exposure, 0.0)
            exposure_rows += 1
        if market_exposure is not None:
            market_exposure_sum += max(market_exposure, 0.0)
        bankroll_fraction_sum += to_float(payload.get("bankroll_fraction"))
        fractional_kelly_sum += to_float(payload.get("fractional_kelly"))
        stake = to_float(payload.get("stake_usd"))
        stake_sum += stake
        rows.append(
            RiskRowResponse(
                market_id=to_text(payload.get("market_id")) or "n/a",
                approved=approved,
                side=to_text(payload.get("side")) or "n/a",
                stake_usd=stake,
                bankroll_fraction=to_float(payload.get("bankroll_fraction")),
                fractional_kelly=to_float(payload.get("fractional_kelly")),
                reasoning=reasoning,
            )
        )
    rows.sort(key=lambda item: item.stake_usd, reverse=True)
    count = len(rows)
    blocked_count = max(count - approved_count, 0)
    approval_rate = (approved_count / count) if count > 0 else 0.0
    blocked_rate = (blocked_count / count) if count > 0 else 0.0
    avg_bankroll_fraction = (bankroll_fraction_sum / count) if count > 0 else 0.0
    avg_fractional_kelly = (fractional_kelly_sum / count) if count > 0 else 0.0
    avg_portfolio_exposure = (portfolio_exposure_sum / exposure_rows) if exposure_rows > 0 else 0.0
    avg_market_exposure = (market_exposure_sum / exposure_rows) if exposure_rows > 0 else 0.0

    panel_status = "ok"
    if daily_stop_triggered or circuit_breaker_active:
        panel_status = "critical"
    elif blocked_count > 0 or len(guardrail_counter) > 0:
        panel_status = "warning"

    anomalies: list[IncidentBannerResponse] = []
    if daily_stop_triggered:
        anomalies.append(
            IncidentBannerResponse(
                level="danger",
                code="risk_daily_stop_triggered",
                title="Risk daily stop was triggered",
                detail="At least one risk decision hit daily stop logic in this run.",
                recommendation="Pause approvals and validate PnL/risk regime before resuming normal operations.",
            )
        )
    if circuit_breaker_active:
        anomalies.append(
            IncidentBannerResponse(
                level="danger",
                code="risk_circuit_breaker_active",
                title="Global circuit breaker is active",
                detail="Risk decisions indicate circuit breaker active state.",
                recommendation="Resolve incident conditions before enabling execution workflows.",
            )
        )
    if blocked_rate >= 0.50 and count > 0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="risk_block_rate_high",
                title="High risk block rate",
                detail=f"blocked_rate={blocked_rate:.2f} blocked={blocked_count}/{count}",
                recommendation="Review edge/confidence and exposure guardrails before changing thresholds.",
            )
        )

    return RiskTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=count > 0,
        note="risk_data_loaded" if count > 0 else "risk_data_missing",
        panel_status=panel_status,
        decisions_count=count,
        approved_count=approved_count,
        blocked_count=blocked_count,
        avg_stake_usd=round(stake_sum / count, 2) if count > 0 else 0.0,
        proposed_stake_usd=round(proposed_stake_sum, 2),
        approved_stake_usd=round(stake_sum, 2),
        avg_portfolio_exposure_usd=round(avg_portfolio_exposure, 2),
        avg_market_exposure_usd=round(avg_market_exposure, 2),
        daily_stop_triggered=daily_stop_triggered,
        circuit_breaker_active=circuit_breaker_active,
        guardrail_distribution=chart_from_counter(guardrail_counter),
        reason_code_distribution=chart_from_counter(reason_counter),
        diagnostics_summary=(
            ChartPointResponse(label="approval_rate", value=float(round(approval_rate, 4))),
            ChartPointResponse(label="blocked_rate", value=float(round(blocked_rate, 4))),
            ChartPointResponse(label="avg_bankroll_fraction", value=float(round(avg_bankroll_fraction, 6))),
            ChartPointResponse(label="avg_fractional_kelly", value=float(round(avg_fractional_kelly, 6))),
            ChartPointResponse(label="avg_portfolio_exposure_usd", value=float(round(avg_portfolio_exposure, 2))),
            ChartPointResponse(label="avg_market_exposure_usd", value=float(round(avg_market_exposure, 2))),
        ),
        anomalies=tuple(anomalies),
        rows=tuple(rows[:30]),
    )


def _extract_reasoning_float(reasoning: tuple[str, ...], key: str) -> float | None:
    prefix = f"{key}="
    for item in reasoning:
        token = item.strip()
        if not token.startswith(prefix):
            continue
        text = token[len(prefix) :].strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None
