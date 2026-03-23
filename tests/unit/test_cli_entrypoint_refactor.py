from __future__ import annotations

from pathlib import Path

import pytest

from prediction_market_bot import main as legacy_main
from prediction_market_bot.cli import app as cli_app
from prediction_market_bot.cli import parser as cli_parser


def test_legacy_main_exports_cli_entrypoint() -> None:
    assert legacy_main.main is cli_app.main
    assert legacy_main.build_parser is cli_parser.build_parser


def test_run_once_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_once_command(
        config_path: Path,
        agents_config_path: Path,
        run_id: str | None = None,
        *,
        force: bool = False,
        profile: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["force"] = force
        captured["profile"] = profile
        return 7

    monkeypatch.setattr(cli_app, "run_once_command", fake_run_once_command)

    exit_code = cli_app.main(
        [
            "run-once",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "run-123",
            "--force",
        ]
    )

    assert exit_code == 7
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "run_id": "run-123",
        "force": True,
        "profile": False,
    }


def test_run_once_dispatch_profile_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_once_command(
        config_path: Path,
        agents_config_path: Path,
        run_id: str | None = None,
        *,
        force: bool = False,
        profile: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["force"] = force
        captured["profile"] = profile
        return 0

    monkeypatch.setattr(cli_app, "run_once_command", fake_run_once_command)

    exit_code = cli_app.main(
        [
            "run-once",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "run-prof",
            "--profile",
        ]
    )

    assert exit_code == 0
    assert captured["profile"] is True


def test_beta_dress_rehearsal_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_beta_dress_rehearsal_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        run_id: str | None,
        operator_id: str,
        approval_rationale: str,
        ui_username: str,
        ui_password: str,
        report_output_path: Path | None,
        skip_scheduler_probe: bool,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["operator_id"] = operator_id
        captured["approval_rationale"] = approval_rationale
        captured["ui_username"] = ui_username
        captured["ui_password"] = ui_password
        captured["report_output_path"] = report_output_path
        captured["skip_scheduler_probe"] = skip_scheduler_probe
        captured["as_json"] = as_json
        return 29

    monkeypatch.setattr(cli_app, "beta_dress_rehearsal_command", fake_beta_dress_rehearsal_command)

    exit_code = cli_app.main(
        [
            "beta-dress-rehearsal",
            "--config",
            "config/app.staging.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "beta-run-42",
            "--operator-id",
            "ops-admin",
            "--approval-rationale",
            "approved in automated rehearsal",
            "--ui-username",
            "operator",
            "--ui-password",
            "operator-pass",
            "--report-output",
            "data/artifacts/reports/beta-run-42.md",
            "--skip-scheduler-probe",
            "--json",
        ]
    )

    assert exit_code == 29
    assert captured == {
        "config_path": Path("config/app.staging.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "run_id": "beta-run-42",
        "operator_id": "ops-admin",
        "approval_rationale": "approved in automated rehearsal",
        "ui_username": "operator",
        "ui_password": "operator-pass",
        "report_output_path": Path("data/artifacts/reports/beta-run-42.md"),
        "skip_scheduler_probe": True,
        "as_json": True,
    }


def test_generate_eval_report_requires_scope() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_app.main(
            [
                "generate-eval-report",
                "--config",
                "config/app.yaml",
                "--agents-config",
                "config/agents.yaml",
            ]
        )
    assert exc_info.value.code == 2


def test_generate_shadow_report_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate_shadow_report_command(
        config_path: Path,
        agents_config_path: Path,
        run_id: str,
        output_path: Path | None = None,
        *,
        as_json: bool = False,
        profile: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["run_id"] = run_id
        captured["output_path"] = output_path
        captured["as_json"] = as_json
        captured["profile"] = profile
        return 13

    monkeypatch.setattr(cli_app, "generate_shadow_report_command", fake_generate_shadow_report_command)

    exit_code = cli_app.main(
        [
            "generate-shadow-report",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--run-id",
            "run-shadow-1",
            "--output",
            "reports/shadow.md",
            "--json",
            "--profile",
        ]
    )

    assert exit_code == 13
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "run_id": "run-shadow-1",
        "output_path": Path("reports/shadow.md"),
        "as_json": True,
        "profile": True,
    }


def test_db_backup_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_db_backup_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        backup_dir: Path | None = None,
        label: str = "",
        as_json: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["backup_dir"] = backup_dir
        captured["label"] = label
        captured["as_json"] = as_json
        return 11

    monkeypatch.setattr(cli_app, "db_backup_command", fake_db_backup_command)

    exit_code = cli_app.main(
        [
            "db-backup",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--backup-dir",
            "data/backups",
            "--label",
            "nightly",
            "--json",
        ]
    )

    assert exit_code == 11
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "backup_dir": Path("data/backups"),
        "label": "nightly",
        "as_json": True,
    }


def test_evaluate_model_promotion_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_evaluate_model_promotion_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None = None,
        benchmark_run_id: str | None = None,
        training_run_id: str | None = None,
        walk_forward_run_id: str | None = None,
        shadow_run_id: str | None = None,
        output_path: Path | None = None,
        as_json: bool = False,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["benchmark_run_id"] = benchmark_run_id
        captured["training_run_id"] = training_run_id
        captured["walk_forward_run_id"] = walk_forward_run_id
        captured["shadow_run_id"] = shadow_run_id
        captured["output_path"] = output_path
        captured["as_json"] = as_json
        return 19

    monkeypatch.setattr(cli_app, "evaluate_model_promotion_command", fake_evaluate_model_promotion_command)

    exit_code = cli_app.main(
        [
            "evaluate-model-promotion",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "historical-markets",
            "--benchmark-run-id",
            "bench-1",
            "--training-run-id",
            "train-1",
            "--walk-forward-run-id",
            "wf-1",
            "--shadow-run-id",
            "shadow-1",
            "--output",
            "data/artifacts/promotion.json",
            "--json",
        ]
    )

    assert exit_code == 19
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "historical-markets",
        "benchmark_run_id": "bench-1",
        "training_run_id": "train-1",
        "walk_forward_run_id": "wf-1",
        "shadow_run_id": "shadow-1",
        "output_path": Path("data/artifacts/promotion.json"),
        "as_json": True,
    }


def test_backfill_historical_markets_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_backfill_historical_markets_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        page_size: int | None,
        max_pages: int | None,
        start_cursor: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        date_from: object,
        date_to: object,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["page_size"] = page_size
        captured["max_pages"] = max_pages
        captured["start_cursor"] = start_cursor
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        captured["as_json"] = as_json
        return 17

    monkeypatch.setattr(cli_app, "backfill_historical_markets_command", fake_backfill_historical_markets_command)

    exit_code = cli_app.main(
        [
            "backfill-historical-markets",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "ds-1",
            "--page-size",
            "123",
            "--max-pages",
            "2",
            "--start-cursor",
            "abc",
            "--checkpoint-path",
            "data/chk.json",
            "--reset-checkpoint",
            "--date-from",
            "2026-01-01",
            "--date-to",
            "2026-01-31",
            "--json",
        ]
    )

    assert exit_code == 17
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["dataset_id"] == "ds-1"
    assert captured["page_size"] == 123
    assert captured["max_pages"] == 2
    assert captured["start_cursor"] == "abc"
    assert captured["checkpoint_path"] == Path("data/chk.json")
    assert captured["reset_checkpoint"] is True
    assert str(captured["date_from"]) == "2026-01-01"
    assert str(captured["date_to"]) == "2026-01-31"
    assert captured["as_json"] is True


def test_backfill_research_evidence_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_backfill_research_evidence_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        corpus_id: str | None,
        source_dataset_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        limit_markets: int | None,
        limit_per_source: int | None,
        decision_date_from: object,
        decision_date_to: object,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["corpus_id"] = corpus_id
        captured["source_dataset_id"] = source_dataset_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["limit_markets"] = limit_markets
        captured["limit_per_source"] = limit_per_source
        captured["decision_date_from"] = decision_date_from
        captured["decision_date_to"] = decision_date_to
        captured["as_json"] = as_json
        return 23

    monkeypatch.setattr(cli_app, "backfill_research_evidence_command", fake_backfill_research_evidence_command)
    exit_code = cli_app.main(
        [
            "backfill-research-evidence",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--corpus-id",
            "corpus-1",
            "--source-dataset-id",
            "ds-1",
            "--checkpoint-path",
            "data/corpus-chk.json",
            "--reset-checkpoint",
            "--limit-markets",
            "10",
            "--limit-per-source",
            "4",
            "--decision-date-from",
            "2026-01-01",
            "--decision-date-to",
            "2026-01-31",
            "--json",
        ]
    )

    assert exit_code == 23
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["corpus_id"] == "corpus-1"
    assert captured["source_dataset_id"] == "ds-1"
    assert captured["checkpoint_path"] == Path("data/corpus-chk.json")
    assert captured["reset_checkpoint"] is True
    assert captured["limit_markets"] == 10
    assert captured["limit_per_source"] == 4
    assert str(captured["decision_date_from"]) == "2026-01-01"
    assert str(captured["decision_date_to"]) == "2026-01-31"
    assert captured["as_json"] is True


def test_backfill_news_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_backfill_news_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        topic: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["topic"] = topic
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["as_json"] = as_json
        return 47

    monkeypatch.setattr(cli_app, "backfill_news_command", fake_backfill_news_command)
    exit_code = cli_app.main(
        [
            "backfill-news",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--corpus-id",
            "news-corpus-1",
            "--checkpoint-path",
            "data/news-chk.json",
            "--reset-checkpoint",
            "--topic",
            "WORLD",
            "--topic",
            "BUSINESS",
            "--keyword",
            "election",
            "--keyword",
            "inflation",
            "--limit-per-query",
            "25",
            "--json",
        ]
    )

    assert exit_code == 47
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "corpus_id": "news-corpus-1",
        "checkpoint_path": Path("data/news-chk.json"),
        "reset_checkpoint": True,
        "topic": ("WORLD", "BUSINESS"),
        "keyword": ("election", "inflation"),
        "limit_per_query": 25,
        "as_json": True,
    }


def test_inspect_news_corpus_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_inspect_news_corpus_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 53

    monkeypatch.setattr(cli_app, "inspect_news_corpus_command", fake_inspect_news_corpus_command)
    exit_code = cli_app.main(
        [
            "inspect-news-corpus",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--corpus-id",
            "news-corpus-2",
            "--checkpoint-path",
            "data/news-chk.json",
            "--json",
        ]
    )

    assert exit_code == 53
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "corpus_id": "news-corpus-2",
        "checkpoint_path": Path("data/news-chk.json"),
        "as_json": True,
    }


def test_verify_news_source_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify_news_source_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        topic: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["topic"] = topic
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 59

    monkeypatch.setattr(cli_app, "verify_news_source_command", fake_verify_news_source_command)
    exit_code = cli_app.main(
        [
            "verify-news-source",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--topic",
            "WORLD",
            "--keyword",
            "election",
            "--limit-per-query",
            "10",
            "--corpus-id",
            "news-corpus-3",
            "--checkpoint-path",
            "data/news-chk.json",
            "--json",
        ]
    )

    assert exit_code == 59
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "topic": ("WORLD",),
        "keyword": ("election",),
        "limit_per_query": 10,
        "corpus_id": "news-corpus-3",
        "checkpoint_path": Path("data/news-chk.json"),
        "as_json": True,
    }


def test_backfill_reddit_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_backfill_reddit_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        subreddit: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        max_pages_per_query: int | None,
        include_comments: bool | None,
        comment_limit_per_post: int | None,
        incremental: bool,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["subreddit"] = subreddit
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["max_pages_per_query"] = max_pages_per_query
        captured["include_comments"] = include_comments
        captured["comment_limit_per_post"] = comment_limit_per_post
        captured["incremental"] = incremental
        captured["as_json"] = as_json
        return 61

    monkeypatch.setattr(cli_app, "backfill_reddit_command", fake_backfill_reddit_command)
    exit_code = cli_app.main(
        [
            "backfill-reddit",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--corpus-id",
            "reddit-corpus-1",
            "--checkpoint-path",
            "data/reddit-chk.json",
            "--reset-checkpoint",
            "--subreddit",
            "worldnews",
            "--keyword",
            "election",
            "--limit-per-query",
            "20",
            "--max-pages-per-query",
            "3",
            "--include-comments",
            "--comment-limit-per-post",
            "7",
            "--incremental",
            "--json",
        ]
    )
    assert exit_code == 61
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "corpus_id": "reddit-corpus-1",
        "checkpoint_path": Path("data/reddit-chk.json"),
        "reset_checkpoint": True,
        "subreddit": ("worldnews",),
        "keyword": ("election",),
        "limit_per_query": 20,
        "max_pages_per_query": 3,
        "include_comments": True,
        "comment_limit_per_post": 7,
        "incremental": True,
        "as_json": True,
    }


def test_verify_reddit_oauth_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify_reddit_oauth_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        subreddit: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        include_comments: bool | None,
        comment_limit_per_post: int | None,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["subreddit"] = subreddit
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["include_comments"] = include_comments
        captured["comment_limit_per_post"] = comment_limit_per_post
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 67

    monkeypatch.setattr(cli_app, "verify_reddit_oauth_command", fake_verify_reddit_oauth_command)
    exit_code = cli_app.main(
        [
            "verify-reddit-oauth",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--subreddit",
            "worldnews",
            "--keyword",
            "inflation",
            "--limit-per-query",
            "5",
            "--include-comments",
            "--comment-limit-per-post",
            "4",
            "--corpus-id",
            "reddit-corpus-2",
            "--checkpoint-path",
            "data/reddit-chk.json",
            "--json",
        ]
    )
    assert exit_code == 67
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "subreddit": ("worldnews",),
        "keyword": ("inflation",),
        "limit_per_query": 5,
        "include_comments": True,
        "comment_limit_per_post": 4,
        "corpus_id": "reddit-corpus-2",
        "checkpoint_path": Path("data/reddit-chk.json"),
        "as_json": True,
    }


def test_backfill_x_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_backfill_x_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        account: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        max_pages_per_query: int | None,
        incremental: bool,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["account"] = account
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["max_pages_per_query"] = max_pages_per_query
        captured["incremental"] = incremental
        captured["as_json"] = as_json
        return 71

    monkeypatch.setattr(cli_app, "backfill_x_command", fake_backfill_x_command)
    exit_code = cli_app.main(
        [
            "backfill-x",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--corpus-id",
            "x-corpus-1",
            "--checkpoint-path",
            "data/x-chk.json",
            "--reset-checkpoint",
            "--account",
            "analyst",
            "--keyword",
            "macro",
            "--limit-per-query",
            "15",
            "--max-pages-per-query",
            "2",
            "--incremental",
            "--json",
        ]
    )
    assert exit_code == 71
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "corpus_id": "x-corpus-1",
        "checkpoint_path": Path("data/x-chk.json"),
        "reset_checkpoint": True,
        "account": ("analyst",),
        "keyword": ("macro",),
        "limit_per_query": 15,
        "max_pages_per_query": 2,
        "incremental": True,
        "as_json": True,
    }


def test_verify_x_source_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify_x_source_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        account: tuple[str, ...],
        keyword: tuple[str, ...],
        limit_per_query: int | None,
        corpus_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["account"] = account
        captured["keyword"] = keyword
        captured["limit_per_query"] = limit_per_query
        captured["corpus_id"] = corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 73

    monkeypatch.setattr(cli_app, "verify_x_source_command", fake_verify_x_source_command)
    exit_code = cli_app.main(
        [
            "verify-x-source",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--account",
            "analyst",
            "--keyword",
            "macro",
            "--limit-per-query",
            "8",
            "--corpus-id",
            "x-corpus-2",
            "--checkpoint-path",
            "data/x-chk.json",
            "--json",
        ]
    )
    assert exit_code == 73
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "account": ("analyst",),
        "keyword": ("macro",),
        "limit_per_query": 8,
        "corpus_id": "x-corpus-2",
        "checkpoint_path": Path("data/x-chk.json"),
        "as_json": True,
    }


def test_build_linkage_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_build_linkage_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        linkage_id: str | None,
        dataset_id: str | None,
        news_corpus_id: str | None,
        reddit_corpus_id: str | None,
        x_corpus_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        limit_evidence: int | None,
        decision_date_from: object,
        decision_date_to: object,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["linkage_id"] = linkage_id
        captured["dataset_id"] = dataset_id
        captured["news_corpus_id"] = news_corpus_id
        captured["reddit_corpus_id"] = reddit_corpus_id
        captured["x_corpus_id"] = x_corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["limit_evidence"] = limit_evidence
        captured["decision_date_from"] = decision_date_from
        captured["decision_date_to"] = decision_date_to
        captured["as_json"] = as_json
        return 79

    monkeypatch.setattr(cli_app, "build_linkage_command", fake_build_linkage_command)
    exit_code = cli_app.main(
        [
            "build-linkage",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--linkage-id",
            "link-1",
            "--dataset-id",
            "hist-ds",
            "--news-corpus-id",
            "news-c1",
            "--reddit-corpus-id",
            "reddit-c1",
            "--x-corpus-id",
            "x-c1",
            "--checkpoint-path",
            "data/link-chk.json",
            "--reset-checkpoint",
            "--limit-evidence",
            "50",
            "--decision-date-from",
            "2026-01-01",
            "--decision-date-to",
            "2026-01-31",
            "--json",
        ]
    )

    assert exit_code == 79
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["linkage_id"] == "link-1"
    assert captured["dataset_id"] == "hist-ds"
    assert captured["news_corpus_id"] == "news-c1"
    assert captured["reddit_corpus_id"] == "reddit-c1"
    assert captured["x_corpus_id"] == "x-c1"
    assert captured["checkpoint_path"] == Path("data/link-chk.json")
    assert captured["reset_checkpoint"] is True
    assert captured["limit_evidence"] == 50
    assert str(captured["decision_date_from"]) == "2026-01-01"
    assert str(captured["decision_date_to"]) == "2026-01-31"
    assert captured["as_json"] is True


def test_inspect_linkage_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_inspect_linkage_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        linkage_id: str | None,
        dataset_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["linkage_id"] = linkage_id
        captured["dataset_id"] = dataset_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 83

    monkeypatch.setattr(cli_app, "inspect_linkage_command", fake_inspect_linkage_command)
    exit_code = cli_app.main(
        [
            "inspect-linkage",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--linkage-id",
            "link-2",
            "--dataset-id",
            "hist-ds",
            "--checkpoint-path",
            "data/link-chk.json",
            "--json",
        ]
    )

    assert exit_code == 83
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "linkage_id": "link-2",
        "dataset_id": "hist-ds",
        "checkpoint_path": Path("data/link-chk.json"),
        "as_json": True,
    }


def test_verify_linkage_quality_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify_linkage_quality_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        linkage_id: str | None,
        dataset_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["linkage_id"] = linkage_id
        captured["dataset_id"] = dataset_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 89

    monkeypatch.setattr(cli_app, "verify_linkage_quality_command", fake_verify_linkage_quality_command)
    exit_code = cli_app.main(
        [
            "verify-linkage-quality",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--linkage-id",
            "link-3",
            "--dataset-id",
            "hist-ds",
            "--checkpoint-path",
            "data/link-chk.json",
            "--json",
        ]
    )

    assert exit_code == 89
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "linkage_id": "link-3",
        "dataset_id": "hist-ds",
        "checkpoint_path": Path("data/link-chk.json"),
        "as_json": True,
    }


def test_run_benchmarks_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_benchmarks_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        labels_path: Path | None,
        split_mode: str,
        train_ratio: float,
        validation_ratio: float,
        train_days: int,
        validation_days: int,
        test_days: int,
        step_days: int,
        max_folds: int,
        min_confidence: float | None,
        min_edge_bps: int | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["labels_path"] = labels_path
        captured["split_mode"] = split_mode
        captured["train_ratio"] = train_ratio
        captured["validation_ratio"] = validation_ratio
        captured["train_days"] = train_days
        captured["validation_days"] = validation_days
        captured["test_days"] = test_days
        captured["step_days"] = step_days
        captured["max_folds"] = max_folds
        captured["min_confidence"] = min_confidence
        captured["min_edge_bps"] = min_edge_bps
        captured["as_json"] = as_json
        return 31

    monkeypatch.setattr(cli_app, "run_benchmarks_command", fake_run_benchmarks_command)
    exit_code = cli_app.main(
        [
            "run-benchmarks",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "hist-ds",
            "--labels-path",
            "data/labels.jsonl",
            "--split-mode",
            "walk-forward",
            "--train-ratio",
            "0.55",
            "--validation-ratio",
            "0.25",
            "--train-days",
            "180",
            "--validation-days",
            "45",
            "--test-days",
            "30",
            "--step-days",
            "15",
            "--max-folds",
            "3",
            "--min-confidence",
            "0.67",
            "--min-edge-bps",
            "250",
            "--json",
        ]
    )

    assert exit_code == 31
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["dataset_id"] == "hist-ds"
    assert captured["labels_path"] == Path("data/labels.jsonl")
    assert captured["split_mode"] == "walk-forward"
    assert captured["train_ratio"] == 0.55
    assert captured["validation_ratio"] == 0.25
    assert captured["train_days"] == 180
    assert captured["validation_days"] == 45
    assert captured["test_days"] == 30
    assert captured["step_days"] == 15
    assert captured["max_folds"] == 3
    assert captured["min_confidence"] == 0.67
    assert captured["min_edge_bps"] == 250
    assert captured["as_json"] is True


def test_build_feature_dataset_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_build_feature_dataset_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        corpus_id: str | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["corpus_id"] = corpus_id
        captured["as_json"] = as_json
        return 37

    monkeypatch.setattr(cli_app, "build_feature_dataset_command", fake_build_feature_dataset_command)
    exit_code = cli_app.main(
        [
            "build-feature-dataset",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "hist-ds",
            "--corpus-id",
            "research-corpus",
            "--json",
        ]
    )

    assert exit_code == 37
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "hist-ds",
        "corpus_id": "research-corpus",
        "as_json": True,
    }


def test_train_baseline_models_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_train_baseline_models_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        feature_rows_path: Path | None,
        split_mode: str,
        train_ratio: float,
        validation_ratio: float,
        window_days: int,
        include_xgboost: bool,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["feature_rows_path"] = feature_rows_path
        captured["split_mode"] = split_mode
        captured["train_ratio"] = train_ratio
        captured["validation_ratio"] = validation_ratio
        captured["window_days"] = window_days
        captured["include_xgboost"] = include_xgboost
        captured["as_json"] = as_json
        return 41

    monkeypatch.setattr(cli_app, "train_baseline_models_command", fake_train_baseline_models_command)
    exit_code = cli_app.main(
        [
            "train-baseline-models",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "hist-ds",
            "--feature-rows-path",
            "data/features.jsonl",
            "--split-mode",
            "holdout",
            "--train-ratio",
            "0.58",
            "--validation-ratio",
            "0.22",
            "--window-days",
            "21",
            "--json",
        ]
    )

    assert exit_code == 41
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["dataset_id"] == "hist-ds"
    assert captured["feature_rows_path"] == Path("data/features.jsonl")
    assert captured["split_mode"] == "holdout"
    assert captured["train_ratio"] == 0.58
    assert captured["validation_ratio"] == 0.22
    assert captured["window_days"] == 21
    assert captured["include_xgboost"] is True
    assert captured["as_json"] is True


def test_generate_model_card_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_generate_model_card_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        run_id: str | None,
        model_name: str | None,
        calibration_run_id: str | None,
        output_path: Path | None,
        owner: str,
        decision: str,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["run_id"] = run_id
        captured["model_name"] = model_name
        captured["calibration_run_id"] = calibration_run_id
        captured["output_path"] = output_path
        captured["owner"] = owner
        captured["decision"] = decision
        captured["as_json"] = as_json
        return 43

    monkeypatch.setattr(cli_app, "generate_model_card_command", fake_generate_model_card_command)
    exit_code = cli_app.main(
        [
            "generate-model-card",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "hist-ds",
            "--run-id",
            "train-1",
            "--model-name",
            "logistic_regression_baseline",
            "--calibration-run-id",
            "cal-1",
            "--output",
            "data/model-card.md",
            "--owner",
            "ml-team",
            "--decision",
            "needs_more_evidence",
            "--json",
        ]
    )

    assert exit_code == 43
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "hist-ds",
        "run_id": "train-1",
        "model_name": "logistic_regression_baseline",
        "calibration_run_id": "cal-1",
        "output_path": Path("data/model-card.md"),
        "owner": "ml-team",
        "decision": "needs_more_evidence",
        "as_json": True,
    }


def test_enrich_alt_data_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_enrich_alt_data_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        enrichment_id: str | None,
        linkage_id: str | None,
        news_corpus_id: str | None,
        reddit_corpus_id: str | None,
        x_corpus_id: str | None,
        checkpoint_path: Path | None,
        reset_checkpoint: bool,
        limit_records: int | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["enrichment_id"] = enrichment_id
        captured["linkage_id"] = linkage_id
        captured["news_corpus_id"] = news_corpus_id
        captured["reddit_corpus_id"] = reddit_corpus_id
        captured["x_corpus_id"] = x_corpus_id
        captured["checkpoint_path"] = checkpoint_path
        captured["reset_checkpoint"] = reset_checkpoint
        captured["limit_records"] = limit_records
        captured["as_json"] = as_json
        return 97

    monkeypatch.setattr(cli_app, "enrich_alt_data_command", fake_enrich_alt_data_command)
    exit_code = cli_app.main(
        [
            "enrich-alt-data",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--enrichment-id",
            "enr-1",
            "--linkage-id",
            "link-1",
            "--news-corpus-id",
            "news-1",
            "--reddit-corpus-id",
            "reddit-1",
            "--x-corpus-id",
            "x-1",
            "--checkpoint-path",
            "data/enrichment-chk.json",
            "--reset-checkpoint",
            "--limit-records",
            "33",
            "--json",
        ]
    )

    assert exit_code == 97
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "enrichment_id": "enr-1",
        "linkage_id": "link-1",
        "news_corpus_id": "news-1",
        "reddit_corpus_id": "reddit-1",
        "x_corpus_id": "x-1",
        "checkpoint_path": Path("data/enrichment-chk.json"),
        "reset_checkpoint": True,
        "limit_records": 33,
        "as_json": True,
    }


def test_inspect_llm_enrichment_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_inspect_llm_enrichment_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        enrichment_id: str | None,
        linkage_id: str | None,
        checkpoint_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["enrichment_id"] = enrichment_id
        captured["linkage_id"] = linkage_id
        captured["checkpoint_path"] = checkpoint_path
        captured["as_json"] = as_json
        return 101

    monkeypatch.setattr(cli_app, "inspect_llm_enrichment_command", fake_inspect_llm_enrichment_command)
    exit_code = cli_app.main(
        [
            "inspect-llm-enrichment",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--enrichment-id",
            "enr-2",
            "--linkage-id",
            "link-2",
            "--checkpoint-path",
            "data/enrichment-chk.json",
            "--json",
        ]
    )

    assert exit_code == 101
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "enrichment_id": "enr-2",
        "linkage_id": "link-2",
        "checkpoint_path": Path("data/enrichment-chk.json"),
        "as_json": True,
    }


def test_build_alt_feature_dataset_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_build_alt_feature_dataset_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        linkage_id: str | None,
        news_corpus_id: str | None,
        reddit_corpus_id: str | None,
        x_corpus_id: str | None,
        enrichment_id: str | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["linkage_id"] = linkage_id
        captured["news_corpus_id"] = news_corpus_id
        captured["reddit_corpus_id"] = reddit_corpus_id
        captured["x_corpus_id"] = x_corpus_id
        captured["enrichment_id"] = enrichment_id
        captured["as_json"] = as_json
        return 103

    monkeypatch.setattr(cli_app, "build_alt_feature_dataset_command", fake_build_alt_feature_dataset_command)
    exit_code = cli_app.main(
        [
            "build-alt-feature-dataset",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "it-ds",
            "--linkage-id",
            "it-linkage",
            "--news-corpus-id",
            "it-news-corpus",
            "--reddit-corpus-id",
            "it-reddit-corpus",
            "--x-corpus-id",
            "it-x-corpus",
            "--enrichment-id",
            "it-enrichment",
            "--json",
        ]
    )

    assert exit_code == 103
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "it-ds",
        "linkage_id": "it-linkage",
        "news_corpus_id": "it-news-corpus",
        "reddit_corpus_id": "it-reddit-corpus",
        "x_corpus_id": "it-x-corpus",
        "enrichment_id": "it-enrichment",
        "as_json": True,
    }


def test_inspect_alt_feature_schema_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_inspect_alt_feature_schema_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["as_json"] = as_json
        return 107

    monkeypatch.setattr(cli_app, "inspect_alt_feature_schema_command", fake_inspect_alt_feature_schema_command)
    exit_code = cli_app.main(
        [
            "inspect-alt-feature-schema",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )

    assert exit_code == 107
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "it-ds",
        "as_json": True,
    }


def test_verify_alt_feature_parity_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify_alt_feature_parity_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["as_json"] = as_json
        return 109

    monkeypatch.setattr(cli_app, "verify_alt_feature_parity_command", fake_verify_alt_feature_parity_command)
    exit_code = cli_app.main(
        [
            "verify-alt-feature-parity",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "it-ds",
            "--json",
        ]
    )

    assert exit_code == 109
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "it-ds",
        "as_json": True,
    }


def test_run_ablation_study_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_ablation_study_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        labels_path: Path | None,
        alt_feature_rows_path: Path | None,
        split_mode: str,
        train_ratio: float,
        validation_ratio: float,
        train_days: int,
        validation_days: int,
        test_days: int,
        step_days: int,
        max_folds: int,
        min_confidence: float | None,
        min_edge_bps: int | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["labels_path"] = labels_path
        captured["alt_feature_rows_path"] = alt_feature_rows_path
        captured["split_mode"] = split_mode
        captured["train_ratio"] = train_ratio
        captured["validation_ratio"] = validation_ratio
        captured["train_days"] = train_days
        captured["validation_days"] = validation_days
        captured["test_days"] = test_days
        captured["step_days"] = step_days
        captured["max_folds"] = max_folds
        captured["min_confidence"] = min_confidence
        captured["min_edge_bps"] = min_edge_bps
        captured["as_json"] = as_json
        return 127

    monkeypatch.setattr(cli_app, "run_ablation_study_command", fake_run_ablation_study_command)
    exit_code = cli_app.main(
        [
            "run-ablation-study",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "it-ds",
            "--labels-path",
            "data/labels.jsonl",
            "--alt-feature-rows-path",
            "data/alt_feature_rows.jsonl",
            "--split-mode",
            "walk-forward",
            "--train-ratio",
            "0.55",
            "--validation-ratio",
            "0.25",
            "--train-days",
            "180",
            "--validation-days",
            "45",
            "--test-days",
            "30",
            "--step-days",
            "15",
            "--max-folds",
            "3",
            "--min-confidence",
            "0.66",
            "--min-edge-bps",
            "220",
            "--json",
        ]
    )

    assert exit_code == 127
    assert captured["config_path"] == Path("config/app.yaml")
    assert captured["agents_config_path"] == Path("config/agents.yaml")
    assert captured["dataset_id"] == "it-ds"
    assert captured["labels_path"] == Path("data/labels.jsonl")
    assert captured["alt_feature_rows_path"] == Path("data/alt_feature_rows.jsonl")
    assert captured["split_mode"] == "walk-forward"
    assert captured["train_ratio"] == 0.55
    assert captured["validation_ratio"] == 0.25
    assert captured["train_days"] == 180
    assert captured["validation_days"] == 45
    assert captured["test_days"] == 30
    assert captured["step_days"] == 15
    assert captured["max_folds"] == 3
    assert captured["min_confidence"] == 0.66
    assert captured["min_edge_bps"] == 220
    assert captured["as_json"] is True


def test_compare_alt_data_variants_dispatch_keeps_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_compare_alt_data_variants_command(
        config_path: Path,
        agents_config_path: Path,
        *,
        dataset_id: str | None,
        run_id: str | None,
        split: str,
        reference_variant: str,
        output_path: Path | None,
        as_json: bool,
    ) -> int:
        captured["config_path"] = config_path
        captured["agents_config_path"] = agents_config_path
        captured["dataset_id"] = dataset_id
        captured["run_id"] = run_id
        captured["split"] = split
        captured["reference_variant"] = reference_variant
        captured["output_path"] = output_path
        captured["as_json"] = as_json
        return 131

    monkeypatch.setattr(cli_app, "compare_alt_data_variants_command", fake_compare_alt_data_variants_command)
    exit_code = cli_app.main(
        [
            "compare-alt-data-variants",
            "--config",
            "config/app.yaml",
            "--agents-config",
            "config/agents.yaml",
            "--dataset-id",
            "it-ds",
            "--run-id",
            "ablation-1",
            "--split",
            "test",
            "--reference-variant",
            "market_plus_research_baseline",
            "--output",
            "reports/ablation.md",
            "--json",
        ]
    )

    assert exit_code == 131
    assert captured == {
        "config_path": Path("config/app.yaml"),
        "agents_config_path": Path("config/agents.yaml"),
        "dataset_id": "it-ds",
        "run_id": "ablation-1",
        "split": "test",
        "reference_variant": "market_plus_research_baseline",
        "output_path": Path("reports/ablation.md"),
        "as_json": True,
    }
