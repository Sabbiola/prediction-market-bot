from prediction_market_bot.main import main


def test_pipeline_smoke() -> None:
    exit_code = main(["run-once", "--config", "config/app.yaml", "--agents-config", "config/agents.yaml"])
    assert exit_code == 0
