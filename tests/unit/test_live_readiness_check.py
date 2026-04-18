"""Smoke test for live-readiness-check CLI (REC-03).

This test guards against regressions in the API surface used by
``live_readiness_check_command``. Previously the command referenced
``settings.storage.data_dir`` (which does not exist) and
``OperatorControlService`` (which does not exist). Both are now fixed — this
test ensures the command runs end-to-end without raising.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from prediction_market_bot.cli.commands.rehearsal_commands import (
    live_readiness_check_command,
)


def test_live_readiness_check_runs_without_stale_api_errors(
    temp_config_paths: tuple[Path, Path],
) -> None:
    app_cfg, agents_cfg = temp_config_paths

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = live_readiness_check_command(
            config_path=app_cfg,
            agents_config_path=agents_cfg,
            as_json=True,
        )

    # Exit code will most likely be 1 (NO_GO) in a clean temp-config setup,
    # but the command must not blow up with AttributeError on stale API refs.
    assert rc in (0, 1)

    payload = json.loads(buf.getvalue())
    assert "go_no_go" in payload
    assert payload["go_no_go"] in ("GO", "NO_GO")
    assert "checks" in payload and isinstance(payload["checks"], list)

    # The "not_paused" check must actually execute — it must not surface
    # the old "operator_control_error" detail caused by the bogus
    # OperatorControlService(...) call.
    details = {c["key"]: c.get("detail", "") for c in payload["checks"]}
    assert "not_paused" in details
    assert "OperatorControlService" not in details["not_paused"]
