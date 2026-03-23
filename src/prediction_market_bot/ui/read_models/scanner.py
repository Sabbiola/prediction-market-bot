from __future__ import annotations

from datetime import UTC, datetime

from prediction_market_bot.domain.enums import MarketStatus
from prediction_market_bot.ui.models import (
    ChartPointResponse,
    IncidentBannerResponse,
    RunSelectorResponse,
    ScannerCandidateRowResponse,
    ScannerTabResponse,
)

from .context import UiRuntimeContext
from .queries import UiReadQueryService
from .shared import (
    as_map,
    chart_from_counter,
    deep_get,
    empty_scanner,
    extract_market_id,
    to_float,
    to_int,
    to_text,
    to_text_tuple,
)


def build_scanner_tab(
    *,
    context: UiRuntimeContext,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> ScannerTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_scanner(selector, "no_run_selected")
    snapshots = queries.artifact_payloads(run, "market_snapshots")
    candidates = queries.artifact_payloads(run, "market_candidates")
    rows: list[ScannerCandidateRowResponse] = []
    reason_counter: dict[str, int] = {}
    rejected_reason_counter: dict[str, int] = {}
    eligible_markets_count = 0
    score_sum = 0.0
    liquidity_sum = 0.0
    volume_sum = 0.0
    spread_sum = 0.0
    hours_sum = 0.0
    scan_settings = context.settings.scan

    for payload in snapshots:
        status = to_text(payload.get("status")).upper()
        liquidity = to_float(payload.get("liquidity_usd"))
        volume = to_float(payload.get("volume_24h_usd"))
        spread = to_int(payload.get("spread_bps"))
        hours_to_resolution = to_float(payload.get("hours_to_resolution"))
        rejected_reasons: list[str] = []
        if status and status != MarketStatus.OPEN.value:
            rejected_reasons.append("market_not_open")
        if liquidity < scan_settings.min_liquidity_usd:
            rejected_reasons.append("liquidity_below_threshold")
        if volume < scan_settings.min_volume_24h_usd:
            rejected_reasons.append("volume_below_threshold")
        if hours_to_resolution < scan_settings.min_hours_to_resolution:
            rejected_reasons.append("too_close_to_resolution")
        if spread > scan_settings.max_spread_bps:
            rejected_reasons.append("spread_above_threshold")
        if rejected_reasons:
            for reason in rejected_reasons:
                rejected_reason_counter[reason] = rejected_reason_counter.get(reason, 0) + 1
        else:
            eligible_markets_count += 1

    for payload in candidates:
        snapshot = as_map(payload.get("market"))
        market_id = extract_market_id(payload) or extract_market_id(snapshot)
        if not market_id:
            continue
        reasons = to_text_tuple(payload.get("reasons"))
        for reason in reasons:
            reason_counter[reason] = reason_counter.get(reason, 0) + 1
        scan_score = to_float(payload.get("scan_score"))
        score_sum += scan_score
        liquidity = to_float(snapshot.get("liquidity_usd"))
        volume = to_float(snapshot.get("volume_24h_usd"))
        spread = to_int(snapshot.get("spread_bps"))
        hours_to_resolution = to_float(snapshot.get("hours_to_resolution"))
        liquidity_sum += liquidity
        volume_sum += volume
        spread_sum += spread
        hours_sum += hours_to_resolution
        rows.append(
            ScannerCandidateRowResponse(
                market_id=market_id,
                title=to_text(deep_get(snapshot, "market", "title")) or "n/a",
                category=to_text(deep_get(snapshot, "market", "category")) or "n/a",
                scan_score=scan_score,
                reasons=reasons,
                liquidity_usd=liquidity,
                volume_24h_usd=volume,
                spread_bps=spread,
                hours_to_resolution=hours_to_resolution,
            )
        )
    rows.sort(key=lambda item: item.scan_score, reverse=True)
    count = len(rows)
    total_markets = len(snapshots)
    rejected_markets_count = max(total_markets - count, 0)
    avg_liquidity = round(liquidity_sum / count, 2) if count > 0 else 0.0
    avg_volume = round(volume_sum / count, 2) if count > 0 else 0.0
    avg_spread = round(spread_sum / count, 2) if count > 0 else 0.0
    avg_hours_to_resolution = round(hours_sum / count, 2) if count > 0 else 0.0
    avg_scan_score = round(score_sum / count, 4) if count > 0 else 0.0
    rejection_ratio = (rejected_markets_count / total_markets) if total_markets > 0 else 0.0

    panel_status = "ok"
    if total_markets <= 0:
        panel_status = "warning"
    elif count <= 0 or rejection_ratio >= 0.80:
        panel_status = "warning"

    anomalies: list[IncidentBannerResponse] = []
    if total_markets > 0 and count == 0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="no_scanner_candidates",
                title="No candidates passed scanner filters",
                detail=f"total_markets={total_markets} rejected={rejected_markets_count}",
                recommendation="Review scanner thresholds and market availability context.",
            )
        )
    if rejection_ratio >= 0.70 and total_markets > 0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="high_scanner_rejection_ratio",
                title="High scanner rejection ratio",
                detail=f"rejection_ratio={rejection_ratio:.2f} rejected={rejected_markets_count}/{total_markets}",
                recommendation="Inspect rejection reasons and adjust scan guardrails only with change control.",
            )
        )
    if count > 0 and avg_spread > (scan_settings.max_spread_bps * 0.90):
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="scanner_spread_near_limit",
                title="Candidate spread near configured limit",
                detail=f"avg_spread_bps={avg_spread:.2f} threshold={scan_settings.max_spread_bps}",
                recommendation="Monitor fill quality and consider tighter scan spread thresholds if degradations persist.",
            )
        )

    return ScannerTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=count > 0 or len(snapshots) > 0,
        note="scanner_data_loaded" if count > 0 else "scanner_data_missing",
        panel_status=panel_status,
        total_markets=total_markets,
        eligible_markets_count=eligible_markets_count,
        rejected_markets_count=rejected_markets_count,
        candidates_count=count,
        avg_scan_score=avg_scan_score,
        funnel_summary=(
            ChartPointResponse(label="total_markets", value=float(total_markets)),
            ChartPointResponse(label="eligible_markets", value=float(eligible_markets_count)),
            ChartPointResponse(label="rejected_markets", value=float(rejected_markets_count)),
            ChartPointResponse(label="candidates", value=float(count)),
        ),
        top_reasons=chart_from_counter(reason_counter),
        rejected_reasons=chart_from_counter(rejected_reason_counter),
        market_context_summary=(
            ChartPointResponse(label="min_liquidity_threshold", value=float(scan_settings.min_liquidity_usd)),
            ChartPointResponse(label="min_volume_threshold", value=float(scan_settings.min_volume_24h_usd)),
            ChartPointResponse(label="max_spread_threshold", value=float(scan_settings.max_spread_bps)),
            ChartPointResponse(label="min_hours_to_resolution_threshold", value=float(scan_settings.min_hours_to_resolution)),
        ),
        diagnostics_summary=(
            ChartPointResponse(label="avg_scan_score", value=float(avg_scan_score)),
            ChartPointResponse(label="avg_liquidity_usd", value=float(avg_liquidity)),
            ChartPointResponse(label="avg_volume_24h_usd", value=float(avg_volume)),
            ChartPointResponse(label="avg_spread_bps", value=float(avg_spread)),
            ChartPointResponse(label="avg_hours_to_resolution", value=float(avg_hours_to_resolution)),
            ChartPointResponse(label="rejection_ratio", value=float(round(rejection_ratio, 4))),
        ),
        anomalies=tuple(anomalies),
        candidates=tuple(rows[:30]),
    )
