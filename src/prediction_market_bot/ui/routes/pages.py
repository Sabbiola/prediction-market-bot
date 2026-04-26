from __future__ import annotations

import glob
import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from prediction_market_bot.ui.dependencies import get_auth_service, get_read_model_service
from prediction_market_bot.ui.auth import UiAuthService
from prediction_market_bot.ui.read_models import UiReadModelService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("UI templates not initialized")
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    user = auth.current_user(request)
    if user is not None:
        return RedirectResponse(url="/", status_code=303)
    next_path = _safe_next_path(request.query_params.get("next"))
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": "Operator Login",
            "auth_enabled": True,
            "auth_user": {},
            "error": "",
            "username": "",
            "next_path": next_path,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_path: str = Form(default="/"),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    if not auth.enabled:
        return RedirectResponse(url="/", status_code=303)
    safe_next = _safe_next_path(next_path)
    attempted_username = username.strip()
    user = auth.authenticate_credentials(username=username, password=password)
    if user is None:
        templates = getattr(request.app.state, "templates", None)
        if not isinstance(templates, Jinja2Templates):
            raise RuntimeError("UI templates not initialized")
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "title": "Operator Login",
                "auth_enabled": True,
                "auth_user": {},
                "error": "Invalid username or password. Check credentials and try again.",
                "username": attempted_username,
                "next_path": safe_next,
            },
            status_code=401,
        )
    auth.login(request, user)
    target = safe_next
    return RedirectResponse(url=target, status_code=303)


@router.post("/logout")
def logout_page(
    request: Request,
    auth: UiAuthService = Depends(get_auth_service),
) -> RedirectResponse:
    auth.logout(request)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    service: UiReadModelService = Depends(get_read_model_service),
    auth: UiAuthService = Depends(get_auth_service),
) -> Response:
    templates = getattr(request.app.state, "templates", None)
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("UI templates not initialized")
    user = auth.current_user(request)
    if auth.enabled and user is None:
        return RedirectResponse(url="/login?next=/", status_code=303)

    run_id = request.query_params.get("run_id")
    review_status = request.query_params.get("review_status")
    review_queue_id = request.query_params.get("review_queue_id")
    tx_intent_id = request.query_params.get("tx_intent_id")
    position_market_id = request.query_params.get("position_market_id")
    active_tab = request.query_params.get("active_tab") or "overview"

    run_selector, run_selector_error = _safe_panel(lambda: service.run_selector(run_id=run_id).model_dump(mode="json"))
    overview, overview_error = _safe_panel(lambda: service.overview_tab(run_id=run_id).model_dump(mode="json"))
    system, system_error = _safe_panel(lambda: service.system_health_tab(run_id=run_id).model_dump(mode="json"))
    scanner, scanner_error = _safe_panel(lambda: service.scanner_tab(run_id=run_id).model_dump(mode="json"))
    research, research_error = _safe_panel(lambda: service.research_tab(run_id=run_id).model_dump(mode="json"))
    prediction, prediction_error = _safe_panel(lambda: service.prediction_tab(run_id=run_id).model_dump(mode="json"))
    risk, risk_error = _safe_panel(lambda: service.risk_tab(run_id=run_id).model_dump(mode="json"))
    execution, execution_error = _safe_panel(lambda: service.execution_tab(run_id=run_id).model_dump(mode="json"))
    positions, positions_error = _safe_panel(
        lambda: service.positions_tab(run_id=run_id, market_id=position_market_id).model_dump(mode="json")
    )
    settlement, settlement_error = _safe_panel(lambda: service.settlement_tab(run_id=run_id).model_dump(mode="json"))
    review_queue, review_queue_error = _safe_panel(
        lambda: service.review_queue_tab(
            run_id=run_id,
            status=review_status,
            queue_id=review_queue_id,
        ).model_dump(mode="json")
    )
    sandbox_tx, sandbox_tx_error = _safe_panel(
        lambda: service.sandbox_tx_tab(run_id=run_id, intent_id=tx_intent_id).model_dump(mode="json")
    )
    reports, reports_error = _safe_panel(lambda: service.reports_tab(run_id=run_id).model_dump(mode="json"))

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "Prediction Market Bot Control Plane",
            "auth_enabled": auth.enabled,
            "auth_user": {
                "username": user.username if user is not None else "",
                "role": user.role if user is not None else "",
                "session_expires_at": user.session_expires_at if user is not None else "",
            },
            "run_id": run_id or "",
            "active_tab": active_tab,
            "review_status": review_status or "PENDING_REVIEW",
            "review_queue_id": review_queue_id or "",
            "tx_intent_id": tx_intent_id or "",
            "position_market_id": position_market_id or "",
            "run_selector": run_selector,
            "run_selector_error": run_selector_error,
            "overview": overview,
            "overview_error": overview_error,
            "system": system,
            "system_error": system_error,
            "scanner": scanner,
            "scanner_error": scanner_error,
            "research": research,
            "research_error": research_error,
            "prediction": prediction,
            "prediction_error": prediction_error,
            "risk": risk,
            "risk_error": risk_error,
            "execution": execution,
            "execution_error": execution_error,
            "positions": positions,
            "positions_error": positions_error,
            "settlement": settlement,
            "settlement_error": settlement_error,
            "review_queue": review_queue,
            "review_queue_error": review_queue_error,
            "sandbox_tx": sandbox_tx,
            "sandbox_tx_error": sandbox_tx_error,
            "reports": reports,
            "reports_error": reports_error,
        },
    )


def _safe_panel(loader: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], str]:
    try:
        return loader(), ""
    except Exception:
        return {}, "panel_temporarily_unavailable"


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
