from __future__ import annotations

import json
from pathlib import Path

from prediction_market_bot.app.config import load_settings
from prediction_market_bot.domain.enums import OutcomeSide
from prediction_market_bot.domain.models import OrderIntent
from prediction_market_bot.main import build_persistence, main
from prediction_market_bot.services import PaperPortfolioEngine


def test_paper_portfolio_state_command_prints_current_state(
    temp_config_paths: tuple[Path, Path],
    deterministic_run_id: str,
    capsys: object,
) -> None:
    app_cfg, agents_cfg = temp_config_paths
    settings = load_settings(app_cfg, agents_cfg)
    persistence = build_persistence(settings)
    engine = PaperPortfolioEngine(persistence=persistence)

    engine.simulate_order_fill(
        run_id=deterministic_run_id,
        order=OrderIntent(
            market_id="cli-market-1",
            venue="polymarket",
            side=OutcomeSide.YES,
            stake_usd=100.0,
            limit_price=0.5,
            rationale="cli-test",
        ),
    )
    engine.mark_market(run_id=deterministic_run_id, market_id="cli-market-1", yes_price=0.6)

    exit_code = main(
        [
            "paper-portfolio-state",
            "--config",
            str(app_cfg),
            "--agents-config",
            str(agents_cfg),
            "--run-id",
            deterministic_run_id,
        ]
    )
    assert exit_code == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["position_count"] == 1
    assert payload["realized_pnl_usd"] == 0.0
    assert payload["unrealized_pnl_usd"] == 20.0
    assert payload["market_exposure_usd"]["cli-market-1"] == 120.0
