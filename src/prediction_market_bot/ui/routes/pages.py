"""Pages router — Fase 7 SPA cutover.

GET /        → serve React SPA (static/dist/index.html)
GET /login   → redirect to / (SPA handles auth)
POST /login  → legacy form-post auth (kept for backward-compat; SPA uses /api/auth/login)
POST /logout → form logout redirect (SPA uses /api/auth/logout)
"""
from __future__ import annotations

import glob
import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from fastapi.templating import Jinja2Templates

from prediction_market_bot.ui.dependencies import get_auth_service, get_read_model_service
from prediction_market_bot.ui.auth import UiAuthService
from prediction_market_bot.ui.read_models import UiReadModelService

logger = logging.getLogger(__name__)

router = APIRouter()

_SPA_INDEX = Path(__file__).resolve().parent.parent / "static" / "dist" / "index.html"


def _serve_spa() -> Response:
    if _SPA_INDEX.exists():
        return FileResponse(str(_SPA_INDEX), media_type="text/html")
    return HTMLResponse(
        "<p>UI not built. Run <code>npm run build</code> inside <code>src/prediction_market_bot/ui/frontend/</code>.</p>",
        status_code=503,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:  # noqa: ARG001
    return _serve_spa()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:  # noqa: ARG001
    # SPA handles the login UI; redirect browser to root so SPA boots.
    return RedirectResponse(url="/", status_code=302)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/"),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    """Legacy form-based login — kept so curl/old bookmarks still work.

    The React SPA uses POST /api/auth/login (JSON) instead.
    """
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    safe_next = _safe_next_path(next_path)
    user = auth.authenticate_credentials(username=username, password=password)
    if user is None:
        return RedirectResponse(url="/login?error=1", status_code=303)
    auth.login(request, user)
    return RedirectResponse(url=safe_next, status_code=303)


@router.post("/logout")
def logout_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Legacy form-based logout. SPA uses POST /api/auth/logout."""
    auth.logout(request)
    return RedirectResponse(url="/", status_code=303)


def _safe_next_path(next_path: str | None) -> str:
    if next_path is None:
        return "/"
    value = next_path.strip()
    if not value.startswith("/"):
        return "/"
    if value.startswith("//"):
        return "/"
    return value or "/"


@router.get("/trader", response_class=HTMLResponse)
def trader_view(
    request: Request,
    service: UiReadModelService = Depends(get_read_model_service),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    """Simple trader-focused dashboard: P&L, balance, equity curve, trades."""
    templates = getattr(request.app.state, "templates", None)
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("UI templates not initialized")

    # Determine reports directory from the context
    context_obj = getattr(request.app.state, "ui_context", None)
    reports_dir = Path("data/artifacts/reports")
    if context_obj is not None:
        settings = getattr(context_obj, "settings", None)
        base = getattr(settings, "base_data_dir", None) if settings else None
        if base:
            candidate = Path(str(base)) / "artifacts" / "reports"
            if candidate.exists():
                reports_dir = candidate

    starting_balance = 500.0
    trades: list[dict[str, Any]] = []
    balance_history: list[float] = [starting_balance]
    cumulative_pnl = 0.0
    total_runs = 0
    runtime_mode = "PAPER"

    # ------------------------------------------------------------------
    # Async settlement index (cross-run)
    # ------------------------------------------------------------------
    # With settlement_same_run=false the per-run JSON report is written BEFORE
    # the market is actually resolved on Polymarket/UMA. The real WIN/LOSS
    # outcomes land later in the ``settlement_results`` artifact via the
    # scheduler's cross-run settlement pass. Pull those first and index by
    # market_id so we can overlay them on top of the execution rows below.
    async_settlements_by_market: dict[str, dict[str, Any]] = {}
    try:
        persistence_obj = getattr(context_obj, "persistence", None) if context_obj is not None else None
        if persistence_obj is not None:
            for row in persistence_obj.read_all_artifact_records("settlement_results"):
                payload = row.get("payload") or {}
                mid = payload.get("market_id")
                outcome = payload.get("outcome_classification", "")
                if not mid or outcome not in ("WIN", "LOSS"):
                    continue
                async_settlements_by_market[mid] = {
                    "pnl_usd": payload.get("pnl_usd", 0.0),
                    "outcome_classification": outcome,
                    "stake_usd": payload.get("stake_usd", 0.0),
                    "side": payload.get("side", ""),
                    "resolved_yes": payload.get("resolved_yes"),
                    "run_id": row.get("run_id"),
                    "timestamp": row.get("timestamp"),
                }
    except Exception:
        # UI must degrade gracefully if the persistence layer is unavailable.
        async_settlements_by_market = {}

    report_files = sorted(glob.glob(str(reports_dir / "paper-*.json")))
    if not report_files:
        report_files = sorted(glob.glob(str(reports_dir / "*.json")))

    # Each paper run re-evaluates the same markets on live data.
    # Keep only the latest settlement per market_id to avoid counting duplicates.
    latest_by_market: dict[str, dict[str, Any]] = {}

    for fpath in report_files:
        try:
            with open(fpath) as fh:
                report = json.load(fh)
        except Exception:
            continue

        total_runs += 1
        obs = report.get("observability", {})
        runtime_mode = obs.get("runtime_mode", runtime_mode) if obs else runtime_mode

        for rec in report.get("records", []):
            execution = rec.get("execution") or {}
            settlement = rec.get("settlement") or {}
            prediction = rec.get("prediction") or {}
            scan = rec.get("scan") or {}
            risk = rec.get("risk") or {}

            if execution.get("status") != "FILLED":
                continue

            market_id = rec.get("market_id", "")
            # Prefer the async (real UMA) settlement over the inline report one.
            # Inline settlements are either absent (settlement_same_run=false) or
            # come from the legacy hash-fake era.
            async_row = async_settlements_by_market.get(market_id)
            if async_row is not None:
                outcome = async_row["outcome_classification"]
                pnl = async_row["pnl_usd"]
                stake = async_row["stake_usd"] or settlement.get("stake_usd") or risk.get("stake_usd", 0)
                side = async_row["side"] or prediction.get("selected_side", "")
            else:
                outcome = settlement.get("outcome_classification", "")
                if outcome not in ("WIN", "LOSS"):
                    continue  # no resolution yet — skip this pending trade
                pnl = settlement.get("pnl_usd", 0.0)
                stake = settlement.get("stake_usd", risk.get("stake_usd", 0))
                side = prediction.get("selected_side", settlement.get("side", ""))

            market_info = scan.get("market", {}).get("market", {})

            latest_by_market[market_id] = {
                "market_id": market_id,
                "title": market_info.get("title", market_id or "Unknown"),
                "side": side,
                "stake": stake,
                "pnl": pnl,
                "outcome": outcome,
                "edge_bps": prediction.get("edge_bps", 0),
                "confidence": prediction.get("confidence", 0),
            }

    # Build final trade list sorted by PnL descending (wins first) for readability
    trades = list(latest_by_market.values())
    # Compute cumulative balance
    cumulative_pnl = 0.0
    for t in trades:
        cumulative_pnl += t["pnl"]
        t["balance_after"] = starting_balance + cumulative_pnl
        balance_history.append(starting_balance + cumulative_pnl)


    # Compute metrics
    total_trades = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    total_pnl = cumulative_pnl
    balance = starting_balance + total_pnl

    gross_wins = sum(t["pnl"] for t in trades if t["outcome"] == "WIN")
    gross_losses = abs(sum(t["pnl"] for t in trades if t["outcome"] == "LOSS"))
    avg_win = gross_wins / wins if wins > 0 else 0.0
    avg_loss = -(gross_losses / losses) if losses > 0 else 0.0
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0
    avg_stake = sum(t["stake"] for t in trades) / total_trades if total_trades > 0 else 0.0

    # Max drawdown
    peak = starting_balance
    max_dd = 0.0
    for b in balance_history:
        if b > peak:
            peak = b
        dd = peak - b
        if dd > max_dd:
            max_dd = dd

    # Best win streak
    best_streak = 0
    current_streak = 0
    for t in trades:
        if t["outcome"] == "WIN":
            current_streak += 1
            best_streak = max(best_streak, current_streak)
        else:
            current_streak = 0

    return templates.TemplateResponse(
        request=request,
        name="trader.html",
        context={
            "title": "PMBot — Trader Dashboard",
            "runtime_mode": runtime_mode,
            "starting_balance": starting_balance,
            "balance": balance,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "total_runs": total_runs,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown": max_dd,
            "avg_stake": avg_stake,
            "best_streak": best_streak,
            "balance_history": balance_history,
            "trades": trades,
        },
    )


# ── Catch-all: serve SPA for any unmatched GET route ──────────────────────────
# REGISTERED LAST so specific routes (/trader, /login, /api/...) take precedence.
@router.get("/{path:path}", response_class=HTMLResponse)
def spa_catchall(path: str) -> Response:  # noqa: ARG001
    return _serve_spa()
