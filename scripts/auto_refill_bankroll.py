"""Auto-refill bankroll watcher.

Monitors ``staging_rehearsal.db`` (or runtime.db) every 60s. When
``realized_pnl_usd`` drives the effective balance (bankroll + PnL)
to ≤ AUTO_REFILL_THRESHOLD_USD, resets ``realized_pnl_usd`` to 0 —
effectively "topping up" the bankroll back to its starting value.

Designed to run continuously alongside the scheduler in paper-test
mode: keeps the bot trading indefinitely so we can collect statistics
on the full distribution of trades, win rates, edge, etc.

Configuration via env vars (with defaults):
  PMB_DB_PATH              path to sqlite DB (default: data/staging_rehearsal.db)
  PMB_BANKROLL_USD         starting bankroll (default: 500.0)
  PMB_REFILL_THRESHOLD_USD refill when balance ≤ this (default: 50.0)
  PMB_POLL_INTERVAL_SEC    check interval (default: 60.0)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("auto_refill_bankroll")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def check_and_refill(db_path: Path, bankroll: float, threshold: float) -> bool:
    """Returns True if refill happened."""
    if not db_path.exists():
        logger.warning("db missing: %s", db_path)
        return False
    try:
        con = sqlite3.connect(str(db_path), timeout=10.0)
        cur = con.cursor()
        cur.execute(
            "SELECT id, payload_json FROM open_positions_state "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            con.close()
            return False
        row_id, pj = row
        p = json.loads(pj)
        realized = float(p.get("realized_pnl_usd", 0.0) or 0.0)
        balance = bankroll + realized
        logger.info(
            "snapshot: realized=%.2f balance=%.2f threshold=%.2f settled=%d",
            realized, balance, threshold, p.get("settled_position_count", 0),
        )
        if balance > threshold:
            con.close()
            return False

        # Refill: zero realized_pnl, keep settled count for audit
        old_realized = realized
        p["realized_pnl_usd"] = 0.0
        p["total_pnl_usd"] = float(p.get("unrealized_pnl_usd", 0.0) or 0.0)
        p["as_of"] = _now_iso()
        new_pj = json.dumps(p)
        cur.execute(
            "UPDATE open_positions_state SET updated_at = ?, payload_json = ? WHERE id = ?",
            (_now_iso(), new_pj, row_id),
        )
        con.commit()
        con.close()
        logger.warning(
            "REFILL TRIGGERED: realized %.2f -> 0.00 (balance was %.2f, ≤ %.2f)",
            old_realized, balance, threshold,
        )
        return True
    except sqlite3.Error as exc:
        logger.error("db error: %s", exc)
        return False


def main() -> int:
    db_path = Path(os.environ.get("PMB_DB_PATH", "data/staging_rehearsal.db"))
    bankroll = float(os.environ.get("PMB_BANKROLL_USD", "500.0"))
    threshold = float(os.environ.get("PMB_REFILL_THRESHOLD_USD", "50.0"))
    interval = float(os.environ.get("PMB_POLL_INTERVAL_SEC", "60.0"))

    logger.info(
        "auto_refill watcher starting db=%s bankroll=%.2f threshold=%.2f interval=%.1fs",
        db_path, bankroll, threshold, interval,
    )

    while True:
        try:
            check_and_refill(db_path, bankroll, threshold)
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected error: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
