"""Live trader monitor endpoint.

Returns the current state of open positions, enriched with real-time
Polymarket prices and slot countdown — so the /trader page can render
a live position monitor without depending on the cross-run JSON
artifact pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()


# Cache Polymarket lookups for a few seconds so rapid polls don't hammer
# the Gamma API.  Map[market_id] -> (fetched_at_unix, payload_dict).
_PRICE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PRICE_CACHE_TTL_SEC = 4.0
_GAMMA_TIMEOUT_SEC = 4.0
_GAMMA_BASE = "https://gamma-api.polymarket.com/markets"

# BTC spot — refreshed every poll cycle (cache short).
_BTC_SPOT_CACHE: dict[str, tuple[float, float]] = {}  # key="spot" -> (ts, price)
_BTC_SPOT_TTL_SEC = 3.0

# Slot-start BTC anchor — once a slot has started, the anchor never
# changes, so cache forever.  Key = slot_start_iso (minute precision).
_SLOT_ANCHOR_CACHE: dict[str, float] = {}


_SLOT_TITLE_RE = re.compile(
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<min>\d{2})\s*(?P<ampm>AM|PM)?",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _resolve_db_path(request: Request) -> Path:
    context = getattr(request.app.state, "ui_context", None)
    if context is None:
        return Path("data/runtime.db")
    settings = getattr(context, "settings", None)
    storage = getattr(settings, "storage", None) if settings else None
    db_path = getattr(storage, "operational_db_path", None) if storage else None
    if db_path:
        candidate = Path(str(db_path))
        if candidate.exists():
            return candidate
    return Path("data/runtime.db")


def _read_latest_open_positions_state(db_path: Path) -> dict[str, Any] | None:
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT payload_json FROM open_positions_state ORDER BY updated_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        con.close()
    except sqlite3.Error as exc:
        logger.warning("trader_live_db_error: %s", exc)
        return None
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _fetch_market(market_id: str) -> dict[str, Any] | None:
    now = time.time()
    cached = _PRICE_CACHE.get(market_id)
    if cached and (now - cached[0]) < _PRICE_CACHE_TTL_SEC:
        return cached[1]
    url = f"{_GAMMA_BASE}/{market_id}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "pmbot-ui/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_GAMMA_TIMEOUT_SEC) as resp:  # nosec B310
            raw = resp.read(1_048_576)
        payload = json.loads(raw)
    except (urllib.error.URLError, ValueError, TimeoutError):
        return cached[1] if cached else None
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None
    _PRICE_CACHE[market_id] = (now, payload)
    return payload


def _parse_outcome_prices(value: Any) -> tuple[float | None, float | None]:
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None, None
    if not isinstance(raw, list) or len(raw) < 2:
        return None, None
    try:
        yes = float(raw[0])
        no = float(raw[1])
    except (ValueError, TypeError):
        return None, None
    return yes, no


def _parse_slot_close_iso(market_payload: dict[str, Any]) -> str | None:
    """Pick the most reliable close timestamp from the Gamma payload.

    BTC Up/Down series markets expose ``endDate`` (ISO Z timestamp) — use
    that directly. Fall back to parsing the slot title (e.g. "Bitcoin Up
    or Down - April 22 16:15 UTC")."""
    end_date = market_payload.get("endDate") or market_payload.get("end_date_iso")
    if isinstance(end_date, str) and end_date.strip():
        return end_date.strip()
    title = market_payload.get("question") or market_payload.get("title") or ""
    if not isinstance(title, str):
        return None
    m = _SLOT_TITLE_RE.search(title)
    if not m:
        return None
    month_name = m.group("month").lower()
    if month_name not in _MONTHS:
        return None
    month = _MONTHS[month_name]
    day = int(m.group("day"))
    hour = int(m.group("hour"))
    minute = int(m.group("min"))
    ampm = (m.group("ampm") or "").upper()
    if ampm == "PM" and hour < 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    now = datetime.now(UTC)
    year = now.year
    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=UTC)
    except ValueError:
        return None
    # If the parsed slot is more than 30 days behind us, assume next year.
    if dt < now - timedelta(days=30):
        dt = dt.replace(year=year + 1)
    return dt.isoformat()


def _fetch_btc_spot() -> float | None:
    """Live BTC/USD spot from Coinbase. Cached for a few seconds."""
    now = time.time()
    cached = _BTC_SPOT_CACHE.get("spot")
    if cached and (now - cached[0]) < _BTC_SPOT_TTL_SEC:
        return cached[1]
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pmbot-ui/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_GAMMA_TIMEOUT_SEC) as resp:  # nosec B310
            payload = json.loads(resp.read(65536))
        amount = payload.get("data", {}).get("amount")
        if amount is None:
            return cached[1] if cached else None
        price = float(amount)
    except (urllib.error.URLError, ValueError, TimeoutError, KeyError):
        return cached[1] if cached else None
    _BTC_SPOT_CACHE["spot"] = (now, price)
    return price


def _fetch_slot_anchor_price(slot_start_iso: str) -> float | None:
    """BTC price at the start of the slot.

    Polymarket BTC Up/Down 15m markets resolve based on the *open* of the
    slot's first 1-minute candle.  We fetch that candle from Coinbase
    Exchange (no auth, very fast).  Once the slot has started, the
    answer is immutable so we cache forever.
    """
    if not slot_start_iso:
        return None
    cached = _SLOT_ANCHOR_CACHE.get(slot_start_iso)
    if cached is not None:
        return cached
    try:
        cleaned = slot_start_iso.replace("Z", "+00:00")
        slot_start = datetime.fromisoformat(cleaned)
        if slot_start.tzinfo is None:
            slot_start = slot_start.replace(tzinfo=UTC)
    except ValueError:
        return None
    # Coinbase Exchange candles: granularity 60s, start/end ISO 8601.
    end = slot_start + timedelta(minutes=2)
    url = (
        "https://api.exchange.coinbase.com/products/BTC-USD/candles"
        f"?granularity=60&start={slot_start.isoformat().replace('+00:00','Z')}"
        f"&end={end.isoformat().replace('+00:00','Z')}"
    )
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "pmbot-ui/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=_GAMMA_TIMEOUT_SEC) as resp:  # nosec B310
            candles = json.loads(resp.read(65536))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    if not isinstance(candles, list) or not candles:
        return None
    # Coinbase returns candles sorted DESC: [[time, low, high, open, close, vol], ...]
    # We want the candle whose `time` matches slot_start (Unix seconds).
    target_unix = int(slot_start.timestamp())
    chosen = None
    for c in candles:
        if isinstance(c, list) and len(c) >= 4 and int(c[0]) == target_unix:
            chosen = c
            break
    if chosen is None:
        # fall back to the earliest candle in the response
        chosen = sorted(candles, key=lambda x: x[0])[0]
    try:
        anchor = float(chosen[3])  # open
    except (ValueError, TypeError, IndexError):
        return None
    _SLOT_ANCHOR_CACHE[slot_start_iso] = anchor
    return anchor


def _seconds_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        # tolerate "...Z"
        cleaned = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    except ValueError:
        return None
    now = datetime.now(UTC)
    return int(round((dt - now).total_seconds()))


def _build_trader_response(db_path: Path) -> dict[str, Any]:
    """Shared implementation for both /api/trader/live/a and /api/trader/live/b."""
    state = _read_latest_open_positions_state(db_path) or {}

    btc_spot = _fetch_btc_spot()

    positions_in: list[dict[str, Any]] = state.get("positions") or []
    positions_out: list[dict[str, Any]] = []

    for pos in positions_in:
        market_id = str(pos.get("market_id") or "").strip()
        side = str(pos.get("side") or "").upper()
        shares = float(pos.get("shares") or 0.0)
        entry_price = float(pos.get("avg_entry_price") or 0.0)
        cost_basis = float(pos.get("cost_basis_usd") or (shares * entry_price))

        market_payload = _fetch_market(market_id) if market_id else None
        yes_price = no_price = None
        title = ""
        slot_close_iso: str | None = None
        slot_start_iso: str | None = None
        if isinstance(market_payload, dict):
            title = (
                market_payload.get("question")
                or market_payload.get("title")
                or ""
            )
            yes_price, no_price = _parse_outcome_prices(
                market_payload.get("outcomePrices")
            )
            slot_close_iso = _parse_slot_close_iso(market_payload)
            if slot_close_iso:
                try:
                    cleaned = slot_close_iso.replace("Z", "+00:00")
                    end_dt = datetime.fromisoformat(cleaned)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=UTC)
                    start_dt = end_dt - timedelta(minutes=15)
                    slot_start_iso = (
                        start_dt.isoformat().replace("+00:00", "Z")
                    )
                except ValueError:
                    slot_start_iso = None

        slot_anchor_btc = (
            _fetch_slot_anchor_price(slot_start_iso) if slot_start_iso else None
        )
        currently_yes = None
        btc_delta = None
        if btc_spot is not None and slot_anchor_btc is not None:
            btc_delta = btc_spot - slot_anchor_btc
            currently_yes = btc_delta >= 0
        currently_winning = None
        if currently_yes is not None:
            our_side_is_yes = side == "YES"
            currently_winning = our_side_is_yes == currently_yes

        side_mark = None
        if side == "YES" and yes_price is not None:
            side_mark = yes_price
        elif side == "NO" and no_price is not None:
            side_mark = no_price

        unrealized_pnl = (
            (side_mark - entry_price) * shares
            if side_mark is not None and shares > 0
            else 0.0
        )
        market_value = (
            side_mark * shares if side_mark is not None and shares > 0 else cost_basis
        )

        positions_out.append(
            {
                "market_id": market_id,
                "title": title,
                "side": side,
                "shares": round(shares, 6),
                "entry_price": round(entry_price, 4),
                "cost_basis_usd": round(cost_basis, 2),
                "mark_price_yes": yes_price,
                "mark_price_no": no_price,
                "side_mark_price": side_mark,
                "market_value_usd": round(market_value, 2),
                "unrealized_pnl_usd": round(unrealized_pnl, 2),
                "slot_close_iso": slot_close_iso,
                "slot_start_iso": slot_start_iso,
                "seconds_to_close": _seconds_until(slot_close_iso),
                "slot_anchor_btc": slot_anchor_btc,
                "btc_delta_usd": (
                    round(btc_delta, 2) if btc_delta is not None else None
                ),
                "currently_yes": currently_yes,
                "currently_winning": currently_winning,
            }
        )

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "btc_spot_usd": btc_spot,
        "open_count": len(positions_out),
        "settled_count": int(state.get("settled_position_count") or 0),
        "realized_pnl_usd": float(state.get("realized_pnl_usd") or 0.0),
        "unrealized_pnl_usd": round(
            sum(p["unrealized_pnl_usd"] for p in positions_out), 2
        ),
        "total_exposure_usd": round(
            sum(p["cost_basis_usd"] for p in positions_out), 2
        ),
        "positions": positions_out,
    }


_MODEL_B_DB = Path("data/runtime_v5.db")


@router.get("/api/trader/live")
def trader_live(request: Request) -> dict[str, Any]:
    """Snapshot of open positions + live Polymarket prices (Model A / main DB)."""
    return _build_trader_response(_resolve_db_path(request))


@router.get("/api/trader/live/a")
def trader_live_a(request: Request) -> dict[str, Any]:
    """Model A (v4) — same as /api/trader/live, explicit alias."""
    return _build_trader_response(_resolve_db_path(request))


@router.get("/api/trader/live/b")
def trader_live_b() -> dict[str, Any]:
    """Model B (v5) — reads from data/runtime_v5.db (separate bankroll)."""
    return _build_trader_response(_MODEL_B_DB)
