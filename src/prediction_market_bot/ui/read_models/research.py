from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from prediction_market_bot.ui.models import (
    ChartPointResponse,
    IncidentBannerResponse,
    ResearchPacketRowResponse,
    ResearchTabResponse,
    RunSelectorResponse,
)

from .queries import UiReadQueryService
from .shared import as_map, as_sequence, chart_from_counter, empty_research, to_float, to_text


def build_research_tab(
    *,
    queries: UiReadQueryService,
    selector: RunSelectorResponse,
) -> ResearchTabResponse:
    run = selector.selected_run_id
    if not run:
        return empty_research(selector, "no_run_selected")
    packets = queries.artifact_payloads(run, "research_packets")
    rows: list[ResearchPacketRowResponse] = []
    source_counter: dict[str, int] = {}
    findings_total = 0
    evidence_sum = 0.0
    disagreement_sum = 0.0
    freshness_sum = 0.0
    contradiction_sum = 0.0
    source_diversity_sum = 0.0
    effective_credibility_sum = 0.0
    feature_bundle_rows = 0
    for payload in packets:
        findings = as_sequence(payload.get("findings"))
        source_types = sorted({to_text(item.get("source_type")) for item in findings if isinstance(item, Mapping)})
        for source in source_types:
            if source:
                source_counter[source] = source_counter.get(source, 0) + 1
        findings_count = len(findings)
        evidence = to_float(payload.get("evidence_strength"))
        disagreement = to_float(payload.get("disagreement_score"))
        feature_bundle = as_map(payload.get("feature_bundle"))
        freshness_hours = to_float(feature_bundle.get("freshness_hours"))
        contradiction_score = to_float(feature_bundle.get("contradiction_score"))
        source_diversity = to_float(feature_bundle.get("source_diversity"))
        effective_credibility = to_float(feature_bundle.get("effective_credibility"))
        if feature_bundle:
            feature_bundle_rows += 1
            freshness_sum += freshness_hours
            contradiction_sum += contradiction_score
            source_diversity_sum += source_diversity
            effective_credibility_sum += effective_credibility
        rows.append(
            ResearchPacketRowResponse(
                market_id=to_text(payload.get("market_id")) or "n/a",
                findings_count=findings_count,
                evidence_strength=evidence,
                disagreement_score=disagreement,
                weighted_sentiment=to_float(payload.get("weighted_sentiment")),
                source_types=tuple(source_types),
                narrative_summary=to_text(payload.get("narrative_summary")),
            )
        )
        findings_total += findings_count
        evidence_sum += evidence
        disagreement_sum += disagreement
    rows.sort(key=lambda item: item.evidence_strength, reverse=True)
    count = len(rows)
    source_failures_count = len(queries.artifact_payloads(run, "source_failures"))
    avg_freshness_hours = round(freshness_sum / feature_bundle_rows, 2) if feature_bundle_rows > 0 else 0.0
    avg_contradiction_score = round(contradiction_sum / feature_bundle_rows, 4) if feature_bundle_rows > 0 else 0.0
    avg_source_diversity = round(source_diversity_sum / feature_bundle_rows, 4) if feature_bundle_rows > 0 else 0.0
    avg_effective_credibility = round(effective_credibility_sum / feature_bundle_rows, 4) if feature_bundle_rows > 0 else 0.0
    avg_evidence_strength = round(evidence_sum / count, 4) if count > 0 else 0.0
    avg_disagreement_score = round(disagreement_sum / count, 4) if count > 0 else 0.0

    panel_status = "ok"
    if count <= 0:
        panel_status = "warning"
    elif source_failures_count > 0 or avg_evidence_strength < 0.35 or avg_freshness_hours > 48.0:
        panel_status = "warning"

    anomalies: list[IncidentBannerResponse] = []
    if source_failures_count > 0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="research_source_failures_detected",
                title="Research source failures detected",
                detail=f"source_failures={source_failures_count}",
                recommendation="Validate provider/API health and fallback behavior for research ingestion.",
            )
        )
    if count > 0 and avg_freshness_hours > 48.0:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="research_freshness_degraded",
                title="Research freshness is degraded",
                detail=f"avg_freshness_hours={avg_freshness_hours:.2f}",
                recommendation="Investigate stale evidence windows or delayed source updates.",
            )
        )
    if count > 0 and avg_contradiction_score > 0.60:
        anomalies.append(
            IncidentBannerResponse(
                level="warning",
                code="research_contradiction_high",
                title="Research contradiction is elevated",
                detail=f"avg_contradiction_score={avg_contradiction_score:.4f}",
                recommendation="Review conflicting narratives before acting on low-confidence opportunities.",
            )
        )

    return ResearchTabResponse(
        generated_at=datetime.now(UTC).isoformat(),
        run_selector=selector,
        available=count > 0,
        note="research_data_loaded" if count > 0 else "research_data_missing",
        panel_status=panel_status,
        packets_count=count,
        findings_count=findings_total,
        avg_evidence_strength=avg_evidence_strength,
        avg_disagreement_score=avg_disagreement_score,
        source_failures_count=source_failures_count,
        coverage_summary=(
            ChartPointResponse(label="avg_evidence_strength", value=float(avg_evidence_strength)),
            ChartPointResponse(label="avg_freshness_hours", value=float(avg_freshness_hours)),
            ChartPointResponse(label="avg_contradiction_score", value=float(avg_contradiction_score)),
            ChartPointResponse(label="avg_source_diversity", value=float(avg_source_diversity)),
        ),
        source_type_distribution=chart_from_counter(source_counter),
        diagnostics_summary=(
            ChartPointResponse(label="packets_count", value=float(count)),
            ChartPointResponse(label="findings_count", value=float(findings_total)),
            ChartPointResponse(label="source_failures", value=float(source_failures_count)),
            ChartPointResponse(label="avg_disagreement_score", value=float(avg_disagreement_score)),
            ChartPointResponse(label="avg_effective_credibility", value=float(avg_effective_credibility)),
        ),
        anomalies=tuple(anomalies),
        packets=tuple(rows[:30]),
    )
