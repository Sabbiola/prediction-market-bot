"""One-shot setup for Bot H — Bot G strategy applied to SOL.

Clones agents_model_g.yaml + app_model_g_staging.yaml to *_h variants and
patches:
  - target_asset: BTC → SOL (engine fetches RSI from Binance SOLUSDT)
  - hyperliquid.coin: BTC → SOL
  - storage paths: runtime_h.db / events_h.jsonl / artifacts_h
  - HL knobs to the SOL-profitable backtest values (TP 1.2%, SL 2.5%,
    16h hold, leverage 15x — WR 70.9% in test)
  - ml_min_prob: 0.0 (no SOL-specific ML model exists yet; pure RSI signal)

Idempotent: safe to re-run.
"""
import re
import shutil
from pathlib import Path

CFG = Path("/opt/pmbot/config")
src_a = CFG / "agents_model_g.yaml"
src_b = CFG / "app_model_g_staging.yaml"
dst_a = CFG / "agents_model_h.yaml"
dst_b = CFG / "app_model_h_staging.yaml"

shutil.copy(src_a, dst_a)
shutil.copy(src_b, dst_b)
print(f"Cloned → {dst_a.name}, {dst_b.name}")

# ── agents_model_h.yaml ─────────────────────────────────────────────
text = dst_a.read_text()
text = text.replace(
    "# Model G (mean-reversion RSI + ML filter)",
    "# Model H (mean-reversion RSI on SOL — pure technical, no ML filter)",
    1,
)
text = text.replace("name: prediction-market-bot-hl-g", "name: prediction-market-bot-hl-h")

# Inject target_asset under the rsi_ml block (idempotent)
if "rsi_ml_target_asset" not in text:
    text = text.replace(
        "rsi_ml_edge_strength: 0.10",
        "rsi_ml_edge_strength: 0.10\n      rsi_ml_target_asset: SOL",
        1,
    )
else:
    text = re.sub(r"rsi_ml_target_asset:\s*\w+", "rsi_ml_target_asset: SOL", text)

# Disable ML filter (no SOL-trained model)
text = re.sub(r"rsi_ml_min_prob:\s*[\d.]+", "rsi_ml_min_prob: 0.0", text, count=1)
dst_a.write_text(text)
print(f"Patched {dst_a.name}: target=SOL, ml_min_prob=0.0")

# ── app_model_h_staging.yaml ────────────────────────────────────────
text = dst_b.read_text()
text = text.replace("data/runtime_g.db",                 "data/runtime_h.db")
text = text.replace("data/backups/operational-db-g",      "data/backups/operational-db-h")
text = text.replace("data/audit/events_g.jsonl",          "data/audit/events_h.jsonl")
text = text.replace("data/research/findings_g.jsonl",     "data/research/findings_h.jsonl")
text = text.replace("data/artifacts_g",                   "data/artifacts_h")
text = text.replace("metrics_g.prom",                     "metrics_h.prom")

# Hyperliquid: switch coin BTC → SOL + SOL-profitable knobs (TP 1.2%, SL 2.5%,
# 16h hold, leverage 15x; backtest WR 70.9%)
text = text.replace("coin: BTC",                  "coin: SOL")
text = text.replace("take_profit_pct: 0.010",    "take_profit_pct: 0.012", 1)
text = text.replace("stop_loss_pct: 0.020",      "stop_loss_pct: 0.025",   1)
text = text.replace("time_exit_seconds: 28800",  "time_exit_seconds: 57600", 1)  # 16h

dst_b.write_text(text)
print(f"Patched {dst_b.name}: SOL coin + SOL-profitable HL knobs")
print()
print("Final HL block:")
for line in text.splitlines():
    if any(s in line for s in ("coin:", "leverage:", "take_profit", "stop_loss",
                                "time_exit_seconds", "max_size_usd",
                                "data/runtime_h", "HYPERLIQUID")):
        print("  " + line)
