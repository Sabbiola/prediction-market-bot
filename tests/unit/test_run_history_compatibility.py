from __future__ import annotations

from prediction_market_bot.services import history
from prediction_market_bot.services import run_history


def test_run_history_shim_reexports_history_services() -> None:
    assert run_history.replay_run is history.replay_run
    assert run_history.evaluate_window is history.evaluate_window
    assert run_history.list_run_ids is history.list_run_ids
    assert run_history.generate_report_markdown is history.generate_report_markdown
    assert run_history.generate_eval_report_markdown is history.generate_eval_report_markdown
    assert run_history.write_report is history.write_report
    assert run_history.ReplaySummary is history.ReplaySummary
    assert run_history.WindowEvaluation is history.WindowEvaluation
