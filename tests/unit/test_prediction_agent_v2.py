from __future__ import annotations

import json
from pathlib import Path

import pytest

from prediction_market_bot.agents.prediction import PredictionAgent
from prediction_market_bot.agents.prediction_model_runtime import (
    PredictionModelArtifactError,
    PredictionModelArtifactLoader,
)
from prediction_market_bot.app.settings import PredictionSettings
from prediction_market_bot.domain.enums import SourceType
from prediction_market_bot.domain.models import MarketCandidate, MarketSnapshot, ResearchFinding, ResearchPacket


def _write_model_artifact(
    path: Path,
    *,
    feature_schema_version: str = "v1",
    feature_columns: list[str] | None = None,
    required_features: list[str] | None = None,
) -> None:
    resolved_feature_columns = feature_columns or [
        "f_market_yes_price",
        "f_research_weighted_sentiment",
        "f_market_price_distance_0_5",
    ]
    resolved_required_features = required_features if required_features is not None else list(resolved_feature_columns)
    means = [0.5] * len(resolved_feature_columns)
    stds = [0.25] * len(resolved_feature_columns)
    weights = [0.35] * len(resolved_feature_columns)
    payload = {
        "artifact_type": "prediction_model_v2",
        "artifact_version": "v1",
        "model_name": "logistic_regression_baseline",
        "model_version": "train-202603220001",
        "feature_schema_version": feature_schema_version,
        "feature_columns": resolved_feature_columns,
        "required_features": resolved_required_features,
        "algorithm_payload": {
            "algorithm": "logistic_regression",
            "feature_names": resolved_feature_columns,
            "means": means,
            "stds": stds,
            "weights": weights,
            "bias": 0.05,
        },
        "calibration": {
            "artifact_type": "prediction_calibration_v2",
            "artifact_version": "v1",
            "calibration_version": "cal-202603220001",
            "method": "platt",
            "parameters": {"a": 1.0, "b": 0.0},
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _candidate(market_id: str, *, yes_price: float, scan_score: float) -> MarketCandidate:
    snapshot = MarketSnapshot.from_yes_price(
        market_id=market_id,
        venue="polymarket",
        title=f"Prediction market {market_id}",
        yes_price=yes_price,
        liquidity_usd=40_000,
        volume_24h_usd=30_000,
        spread_bps=110,
        hours_to_resolution=16,
        last_price_move_bps=10,
        category="test",
    )
    return MarketCandidate(market=snapshot, scan_score=scan_score, reasons=("ranked",))


def _research_packet(
    market_id: str,
    *,
    weighted_sentiment: float,
    evidence_strength: float,
    disagreement_score: float,
    feature_bundle: dict[str, float] | None = None,
) -> ResearchPacket:
    return ResearchPacket(
        market_id=market_id,
        findings=(
            ResearchFinding(
                source_type=SourceType.RSS,
                source_name="rss",
                summary="mock",
                sentiment=weighted_sentiment,
                credibility=max(min(evidence_strength, 1.0), 0.01),
            ),
        ),
        weighted_sentiment=weighted_sentiment,
        evidence_strength=evidence_strength,
        disagreement_score=disagreement_score,
        narrative_summary="mock",
        feature_bundle=dict(feature_bundle or {}),
    )


def test_prediction_model_artifact_loader_supports_v2_contract(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(artifact)
    loader = PredictionModelArtifactLoader()
    contract = loader.load(artifact)
    assert contract.model_name == "logistic_regression_baseline"
    assert contract.model_version == "train-202603220001"
    raw, calibrated = contract.predict_yes_probability(
        {
            "f_market_yes_price": 0.45,
            "f_research_weighted_sentiment": 0.3,
            "f_market_price_distance_0_5": 0.05,
        }
    )
    assert 0.0 < raw < 1.0
    assert 0.0 < calibrated < 1.0


def test_prediction_agent_v2_fails_on_feature_schema_mismatch_when_fallback_disabled(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(artifact, feature_schema_version="v9")
    agent = PredictionAgent(
        PredictionSettings(
            engine="model_v2",
            model_artifact_path=str(artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=False,
        )
    )
    candidate = _candidate("m-v2-mismatch", yes_price=0.44, scan_score=0.72)
    research = _research_packet(
        "m-v2-mismatch",
        weighted_sentiment=0.3,
        evidence_strength=0.9,
        disagreement_score=0.1,
    )
    with pytest.raises(PredictionModelArtifactError, match="feature_schema_version_mismatch"):
        agent.run(candidate, research)


def test_prediction_agent_v2_is_deterministic_for_same_input(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(artifact)
    agent = PredictionAgent(
        PredictionSettings(
            engine="model_v2",
            model_artifact_path=str(artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=False,
        )
    )
    candidate = _candidate("m-v2-deterministic", yes_price=0.41, scan_score=0.77)
    research = _research_packet(
        "m-v2-deterministic",
        weighted_sentiment=0.2,
        evidence_strength=0.8,
        disagreement_score=0.15,
    )
    first = agent.run(candidate, research)
    second = agent.run(candidate, research)
    assert first == second
    assert any("prediction_engine=model_v2" in item for item in first.rationale)
    assert any("model_version=train-202603220001" in item for item in first.rationale)
    assert any("calibration_version=cal-202603220001" in item for item in first.rationale)


def test_prediction_agent_v2_falls_back_to_heuristic_when_parity_fails(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(
        artifact,
        required_features=[
            "f_market_yes_price",
            "f_research_weighted_sentiment",
            "f_feature_not_present_at_runtime",
        ],
    )
    fallback_agent = PredictionAgent(
        PredictionSettings(
            engine="model_v2",
            model_artifact_path=str(artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
        )
    )
    baseline_agent = PredictionAgent(PredictionSettings())
    candidate = _candidate("m-v2-fallback", yes_price=0.53, scan_score=0.66)
    research = _research_packet(
        "m-v2-fallback",
        weighted_sentiment=0.15,
        evidence_strength=0.72,
        disagreement_score=0.12,
    )
    fallback_result = fallback_agent.run(candidate, research)
    baseline_result = baseline_agent.run(candidate, research)
    assert fallback_result.fair_yes_prob == baseline_result.fair_yes_prob
    assert fallback_result.selected_side == baseline_result.selected_side
    assert fallback_result.confidence == baseline_result.confidence
    assert any("prediction_engine_fallback=heuristic" in item for item in fallback_result.rationale)


def test_prediction_agent_shadow_mode_persists_side_by_side_predictions(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(artifact)
    agent = PredictionAgent(
        PredictionSettings(
            engine="shadow_scoring",
            model_artifact_path=str(artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
        )
    )
    candidate = _candidate("m-shadow-ok", yes_price=0.47, scan_score=0.73)
    research = _research_packet(
        "m-shadow-ok",
        weighted_sentiment=0.18,
        evidence_strength=0.82,
        disagreement_score=0.11,
    )
    result = agent.run(candidate, research)
    comparison = agent.last_shadow_comparison
    assert comparison is not None
    assert any("prediction_engine=shadow_primary_heuristic" in item for item in result.rationale)
    assert comparison["market_id"] == "m-shadow-ok"
    assert comparison["parity_status"] == "ok"
    assert isinstance(comparison["heuristic_prediction"], dict)
    assert isinstance(comparison["model_v2_prediction"], dict)


def test_prediction_agent_shadow_mode_surfaces_parity_mismatch_warning(tmp_path: Path) -> None:
    artifact = tmp_path / "model_artifact.json"
    _write_model_artifact(
        artifact,
        required_features=[
            "f_market_yes_price",
            "f_research_weighted_sentiment",
            "f_feature_not_present_at_runtime",
        ],
    )
    shadow_agent = PredictionAgent(
        PredictionSettings(
            engine="shadow_scoring",
            model_artifact_path=str(artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
        )
    )
    baseline_agent = PredictionAgent(PredictionSettings())
    candidate = _candidate("m-shadow-mismatch", yes_price=0.51, scan_score=0.68)
    research = _research_packet(
        "m-shadow-mismatch",
        weighted_sentiment=0.12,
        evidence_strength=0.77,
        disagreement_score=0.09,
    )
    shadow_result = shadow_agent.run(candidate, research)
    baseline_result = baseline_agent.run(candidate, research)

    assert shadow_result.fair_yes_prob == baseline_result.fair_yes_prob
    assert shadow_result.selected_side == baseline_result.selected_side
    assert shadow_result.confidence == baseline_result.confidence
    assert any("shadow_parity_status=warning" in item for item in shadow_result.rationale)

    comparison = shadow_agent.last_shadow_comparison
    assert comparison is not None
    assert comparison["parity_status"] == "warning"
    assert comparison["model_v2_prediction"] is None
    assert any("feature_parity_check_failed" in warning for warning in shadow_agent.last_parity_warnings)


def test_prediction_agent_shadow_mode_includes_alt_llm_side_by_side_prediction(tmp_path: Path) -> None:
    primary_artifact = tmp_path / "primary_model_artifact.json"
    _write_model_artifact(primary_artifact)
    alt_artifact = tmp_path / "alt_shadow_model_artifact.json"
    _write_model_artifact(
        alt_artifact,
        feature_schema_version="alt-v1",
        feature_columns=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
            "f_alt_novelty_score",
        ],
    )
    agent = PredictionAgent(
        PredictionSettings(
            engine="shadow_scoring",
            model_artifact_path=str(primary_artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
            alt_shadow_enabled=True,
            alt_shadow_model_artifact_path=str(alt_artifact),
            alt_shadow_expected_feature_schema_version="alt-v1",
            alt_shadow_strict_feature_parity=True,
            alt_shadow_required_source_coverage=("news", "reddit", "x"),
            alt_shadow_require_llm_enrichment=True,
        )
    )
    candidate = _candidate("m-shadow-alt", yes_price=0.49, scan_score=0.71)
    research = _research_packet(
        "m-shadow-alt",
        weighted_sentiment=0.2,
        evidence_strength=0.8,
        disagreement_score=0.1,
        feature_bundle={
            "f_alt_evidence_count": 12.0,
            "f_alt_news_volume_24h": 4.0,
            "f_alt_reddit_mentions_24h": 3.0,
            "f_alt_x_mentions_24h": 5.0,
            "f_alt_enrichment_coverage": 0.75,
            "f_alt_novelty_score": 0.62,
        },
    )
    _ = agent.run(candidate, research)
    comparison = agent.last_shadow_comparison
    assert comparison is not None
    assert comparison["parity_status"] == "ok"
    assert isinstance(comparison["alt_llm_shadow_prediction"], dict)
    assert comparison["alt_llm_shadow_status"] == "available"
    approvals = comparison.get("approvals")
    assert isinstance(approvals, dict)
    assert "alt_llm_shadow" in approvals


def test_prediction_agent_shadow_mode_surfaces_alt_llm_coverage_and_enrichment_warnings(tmp_path: Path) -> None:
    primary_artifact = tmp_path / "primary_model_artifact.json"
    _write_model_artifact(primary_artifact)
    alt_artifact = tmp_path / "alt_shadow_model_artifact.json"
    _write_model_artifact(
        alt_artifact,
        feature_schema_version="alt-v1",
        feature_columns=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        required_features=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
    )
    agent = PredictionAgent(
        PredictionSettings(
            engine="shadow_scoring",
            model_artifact_path=str(primary_artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
            alt_shadow_enabled=True,
            alt_shadow_model_artifact_path=str(alt_artifact),
            alt_shadow_expected_feature_schema_version="alt-v1",
            alt_shadow_strict_feature_parity=True,
            alt_shadow_required_source_coverage=("news", "reddit", "x"),
            alt_shadow_require_llm_enrichment=True,
        )
    )
    candidate = _candidate("m-shadow-alt-warning", yes_price=0.52, scan_score=0.67)
    research = _research_packet(
        "m-shadow-alt-warning",
        weighted_sentiment=0.1,
        evidence_strength=0.7,
        disagreement_score=0.08,
        feature_bundle={
            "f_alt_evidence_count": 4.0,
            "f_alt_news_volume_24h": 0.0,
            "f_alt_reddit_mentions_24h": 0.0,
            "f_alt_x_mentions_24h": 0.0,
            "f_alt_enrichment_coverage": 0.0,
        },
    )
    _ = agent.run(candidate, research)
    comparison = agent.last_shadow_comparison
    assert comparison is not None
    assert comparison["parity_status"] == "warning"
    assert comparison["alt_llm_shadow_status"] in {"available_with_parity_warnings", "inference_error"}
    warnings = tuple(agent.last_parity_warnings)
    assert any("missing_source_coverage source=news" in warning for warning in warnings)
    assert any("enrichment_pipeline_failure" in warning for warning in warnings)


def test_prediction_agent_alt_promoted_mode_uses_alt_prediction_when_coverage_is_available(tmp_path: Path) -> None:
    primary_artifact = tmp_path / "primary_model_artifact.json"
    _write_model_artifact(primary_artifact)
    alt_artifact = tmp_path / "alt_promoted_model_artifact.json"
    _write_model_artifact(
        alt_artifact,
        feature_schema_version="alt-v1",
        feature_columns=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        required_features=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
    )
    agent = PredictionAgent(
        PredictionSettings(
            engine="model_v2_alt_promoted",
            model_artifact_path=str(primary_artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
            alt_shadow_enabled=True,
            alt_shadow_model_artifact_path=str(alt_artifact),
            alt_shadow_expected_feature_schema_version="alt-v1",
            alt_shadow_strict_feature_parity=True,
            alt_shadow_required_source_coverage=("news", "reddit", "x"),
            alt_shadow_require_llm_enrichment=True,
        )
    )
    candidate = _candidate("m-alt-promoted", yes_price=0.48, scan_score=0.74)
    research = _research_packet(
        "m-alt-promoted",
        weighted_sentiment=0.21,
        evidence_strength=0.83,
        disagreement_score=0.10,
        feature_bundle={
            "f_alt_news_volume_24h": 5.0,
            "f_alt_reddit_mentions_24h": 2.0,
            "f_alt_x_mentions_24h": 3.0,
            "f_alt_enrichment_coverage": 0.8,
        },
    )
    result = agent.run(candidate, research)
    comparison = agent.last_shadow_comparison

    assert comparison is not None
    assert comparison["comparison_kind"] == "alt_promoted_vs_model_v2_baseline"
    assert comparison["primary_prediction"] == "alt_llm_promoted"
    assert comparison["alt_llm_promoted_status"] == "available"
    assert isinstance(comparison["model_v2_prediction"], dict)
    assert isinstance(comparison["alt_llm_promoted_prediction"], dict)
    assert any("prediction_engine=alt_llm_promoted" in item for item in result.rationale)
    assert any("alt_llm_promoted_active=true" in item for item in result.rationale)


def test_prediction_agent_alt_promoted_mode_falls_back_to_model_v2_when_alt_path_unavailable(tmp_path: Path) -> None:
    primary_artifact = tmp_path / "primary_model_artifact.json"
    _write_model_artifact(primary_artifact)
    alt_artifact = tmp_path / "alt_promoted_model_artifact.json"
    _write_model_artifact(
        alt_artifact,
        feature_schema_version="alt-v1",
        feature_columns=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
        required_features=[
            "f_market_yes_price",
            "f_alt_news_volume_24h",
            "f_alt_reddit_mentions_24h",
            "f_alt_x_mentions_24h",
            "f_alt_enrichment_coverage",
        ],
    )
    agent = PredictionAgent(
        PredictionSettings(
            engine="model_v2_alt_promoted",
            model_artifact_path=str(primary_artifact),
            model_expected_feature_schema_version="v1",
            strict_feature_parity=True,
            fallback_to_heuristic=True,
            alt_shadow_enabled=True,
            alt_shadow_model_artifact_path=str(alt_artifact),
            alt_shadow_expected_feature_schema_version="alt-v1",
            alt_shadow_strict_feature_parity=True,
            alt_shadow_required_source_coverage=("news", "reddit", "x"),
            alt_shadow_require_llm_enrichment=True,
        )
    )
    candidate = _candidate("m-alt-promoted-fallback", yes_price=0.50, scan_score=0.69)
    research = _research_packet(
        "m-alt-promoted-fallback",
        weighted_sentiment=0.13,
        evidence_strength=0.74,
        disagreement_score=0.12,
        feature_bundle={
            "f_alt_news_volume_24h": 0.0,
            "f_alt_reddit_mentions_24h": 0.0,
            "f_alt_x_mentions_24h": 0.0,
            "f_alt_enrichment_coverage": 0.0,
        },
    )
    result = agent.run(candidate, research)
    comparison = agent.last_shadow_comparison

    assert comparison is not None
    assert comparison["comparison_kind"] == "alt_promoted_vs_model_v2_baseline"
    assert comparison["primary_prediction"] == "model_v2"
    assert comparison["parity_status"] == "warning"
    assert comparison["alt_llm_promoted_status"] in {"coverage_missing", "available_with_parity_warnings", "available"}
    assert any("alt_llm_promoted_fallback=model_v2" in item for item in result.rationale)
    assert any("missing_source_coverage source=news" in warning for warning in agent.last_parity_warnings)
