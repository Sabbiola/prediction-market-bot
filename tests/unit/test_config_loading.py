from pathlib import Path

import pytest

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import RuntimeMode


def test_load_settings_from_repository_config() -> None:
    settings = load_settings(
        app_config_path=Path("config/app.yaml"),
        agents_config_path=Path("config/agents.yaml"),
    )
    assert settings.runtime.mode == RuntimeMode.PAPER_LIVE
    assert settings.dry_run is True
    assert settings.prediction.min_confidence > 0.0
    assert settings.prediction.engine == "model_v2"
    assert settings.prediction.fallback_to_heuristic is True
    assert settings.prediction.strict_feature_parity is True
    assert settings.prediction.alt_shadow_promoted_enabled is False
    assert settings.prediction.alt_shadow_promoted_runtime_modes == ("SANDBOX_CHAIN",)
    assert settings.model_promotion.enabled is True
    assert settings.model_promotion.require_explicit_approval_in_live_modes is True
    assert "SANDBOX_CHAIN" in settings.model_promotion.promoted_runtime_modes
    assert settings.alt_data.enabled is False
    assert set(settings.alt_data.sources[i].source_id for i in range(len(settings.alt_data.sources))) == {
        "news_rss_web",
        "reddit",
        "x",
    }
    assert settings.risk.bankroll_usd > 0.0
    assert settings.strategy_research.default_dataset_id == "historical-markets"
    assert settings.strategy_research.research_corpus_default_corpus_id == "historical-research-evidence"
    assert settings.strategy_research.news_corpus_default_corpus_id == "historical-news-corpus"
    assert settings.strategy_research.news_corpus_default_source_id == "news_rss_web"
    assert settings.strategy_research.reddit_corpus_default_corpus_id == "historical-reddit-corpus"
    assert settings.strategy_research.reddit_corpus_default_source_id == "reddit"
    assert settings.strategy_research.x_corpus_default_corpus_id == "historical-x-corpus"
    assert settings.strategy_research.x_corpus_default_source_id == "x"
    assert settings.strategy_research.x_corpus_auth_mode == "bearer"
    assert settings.strategy_research.linkage_default_linkage_id == "historical-evidence-linkage"
    assert settings.strategy_research.linkage_source_dataset_id == "historical-markets"
    assert settings.strategy_research.linkage_news_corpus_id == "historical-news-corpus"
    assert settings.strategy_research.linkage_reddit_corpus_id == "historical-reddit-corpus"
    assert settings.strategy_research.linkage_x_corpus_id == "historical-x-corpus"
    assert settings.strategy_research.linkage_enabled_source_classes == ("news_rss_web", "reddit", "x")
    assert settings.strategy_research.llm_enrichment_default_enrichment_id == "historical-llm-enrichment"
    assert settings.strategy_research.llm_enrichment_default_linkage_id == "historical-evidence-linkage"
    assert settings.strategy_research.llm_enrichment_enabled is False
    assert settings.strategy_research.llm_enrichment_provider == "deterministic"
    assert settings.strategy_research.llm_enrichment_include_states == ("linked", "ambiguous")


def test_rejects_live_mode(tmp_path: Path) -> None:
    app_cfg = tmp_path / "app.yaml"
    agents_cfg = tmp_path / "agents.yaml"

    app_cfg.write_text(
        "\n".join(
            [
                "app:",
                "  mode: live",
                "observability:",
                "  log_level: INFO",
                "  json_logs: true",
                "venue:",
                "  provider: polymarket",
                "  dry_run: true",
                "feature_flags:",
                "  allow_live_execution: false",
            ]
        ),
        encoding="utf-8",
    )
    agents_cfg.write_text("thresholds: {}\nrisk: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported runtime mode"):
        load_settings(app_cfg, agents_cfg)


def test_loads_prediction_model_inference_settings(tmp_path: Path) -> None:
    app_cfg = tmp_path / "app.yaml"
    agents_cfg = tmp_path / "agents.yaml"
    app_cfg.write_text(
        "\n".join(
            [
                "app:",
                "  env: test",
                "runtime:",
                "  mode: DRY_RUN_STATIC",
                "venue:",
                "  provider: polymarket",
                "  dry_run: true",
                "feature_flags:",
                "  allow_live_execution: false",
            ]
        ),
        encoding="utf-8",
    )
    agents_cfg.write_text(
        "\n".join(
            [
                "thresholds: {}",
                "risk: {}",
                "agents:",
                "  prediction:",
                "    components:",
                "      market_weight: 0.4",
                "      narrative_weight: 0.5",
                "      structure_weight: 0.1",
                "    model_inference:",
                "      engine: model_v2",
                "      model_artifact_path: data/models/prediction_model.json",
                "      calibration_artifact_path: data/models/prediction_calibration.json",
                "      feature_schema_version: v1",
                "      strict_feature_parity: true",
                "      fallback_to_heuristic: false",
                "      forced_heuristic_reason: test_override",
                "      alt_shadow:",
                "        enabled: true",
                "        promoted_enabled: true",
                "        promoted_runtime_modes:",
                "          - SANDBOX_CHAIN",
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(app_cfg, agents_cfg)
    assert settings.prediction.engine == "model_v2"
    assert settings.prediction.model_artifact_path == "data/models/prediction_model.json"
    assert settings.prediction.calibration_artifact_path == "data/models/prediction_calibration.json"
    assert settings.prediction.model_expected_feature_schema_version == "v1"
    assert settings.prediction.strict_feature_parity is True
    assert settings.prediction.fallback_to_heuristic is False
    assert settings.prediction.forced_heuristic_reason == "test_override"
    assert settings.prediction.alt_shadow_enabled is True
    assert settings.prediction.alt_shadow_promoted_enabled is True
    assert settings.prediction.alt_shadow_promoted_runtime_modes == ("SANDBOX_CHAIN",)


def test_load_settings_from_staging_profile() -> None:
    settings = load_settings(
        app_config_path=Path("config/app.staging.yaml"),
        agents_config_path=Path("config/agents.yaml"),
    )
    assert settings.runtime.env == "staging"
    assert settings.runtime.mode == RuntimeMode.SANDBOX_CHAIN
    assert settings.execution.mode.value == "SANDBOX_CHAIN"
    assert settings.live_market_data.enabled is True
    assert settings.live_research.enabled is True
    assert settings.execution.blocking_trade_review is True
    assert settings.execution.settlement_same_run is False
    assert settings.sandbox_chain.enabled is True
    assert settings.sandbox_chain.submit_tx is True
    assert settings.ui_auth.enabled is True
    assert settings.ui_auth.require_password_hashes is True
    assert settings.storage.operational_db_driver == "sqlite"
