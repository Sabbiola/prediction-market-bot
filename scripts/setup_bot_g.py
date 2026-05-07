"""One-shot setup for Bot G (RSI mean reversion + ML filter).

Clones agents_model_e.yaml + app_model_e_staging.yaml to *_g variants,
patches the engine to "rsi_ml" with the backtest-validated knobs and
HL setup (TP=1%, SL=2%, hold=8h, leverage 5x).

Idempotent: safe to re-run.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

CFG = Path("/opt/pmbot/config")

src_a = CFG / "agents_model_e.yaml"
src_b = CFG / "app_model_e_staging.yaml"
dst_a = CFG / "agents_model_g.yaml"
dst_b = CFG / "app_model_g_staging.yaml"

shutil.copy(src_a, dst_a)
shutil.copy(src_b, dst_b)
print(f"Cloned → {dst_a.name}, {dst_b.name}")

# ── agents_model_g.yaml ────────────────────────────────────────────
text = dst_a.read_text()
text = text.replace(
    "# Model E (Hyperliquid perp executor) agents config = v5 model + regime gate enabled",
    "# Model G (mean-reversion RSI + ML filter) — backtest-validated profitable strategy",
    1,
)
text = text.replace("name: prediction-market-bot-hl", "name: prediction-market-bot-hl-g")
text = text.replace("engine: model_v2", "engine: rsi_ml", 1)

marker = "      strict_feature_parity: true"
add_block = """      rsi_ml_rsi_low: 25.0
      rsi_ml_rsi_high: 75.0
      rsi_ml_min_prob: 0.30
      rsi_ml_edge_strength: 0.10"""
if "rsi_ml_rsi_low" not in text and marker in text:
    text = text.replace(marker, marker + "\n" + add_block, 1)

dst_a.write_text(text)
print(f"Patched {dst_a.name}: engine=rsi_ml + RSI knobs")

# ── app_model_g_staging.yaml ───────────────────────────────────────
text = dst_b.read_text()
text = text.replace("data/runtime_e.db",          "data/runtime_g.db")
text = text.replace("data/backups/operational-db-e", "data/backups/operational-db-g")
text = text.replace("data/audit/events_e.jsonl",  "data/audit/events_g.jsonl")
text = text.replace("data/research/findings_e.jsonl", "data/research/findings_g.jsonl")
text = text.replace("data/artifacts_e",           "data/artifacts_g")
text = text.replace("metrics_e.prom",             "metrics_g.prom")

old_hl_comment = """  # Setup B: hold 4h with wide TP/SL so each trade clears the 0.09% round-trip
  # fee floor with margin.  At TP=2%/SL=1.2% break-even WR is ~38%, well
  # below the model's historical 45-50%.  The agreement-skip logic in the
  # executor prevents pyramiding redundant orders inside the 4h window."""
new_hl_comment = """  # Setup G (RSI mean reversion + ML filter): TP=1.0%, SL=2.0%, hold=8h,
  # leverage 5x.  Backtest WR 60-66%, Sharpe 5-12, MDD < 10% bankroll.
  # Asymmetric SL (>TP) gives the mean-revert thesis time to play out."""
if old_hl_comment in text:
    text = text.replace(old_hl_comment, new_hl_comment, 1)

text = re.sub(r"leverage: 3\b", "leverage: 5", text)
text = re.sub(r"take_profit_pct: 0\.020\b", "take_profit_pct: 0.010", text)
text = re.sub(r"stop_loss_pct: 0\.012\b",   "stop_loss_pct: 0.020",   text)
text = re.sub(r"time_exit_seconds: 14400\b","time_exit_seconds: 28800", text)
text = re.sub(r"max_size_usd: 150\.0\b",    "max_size_usd: 100.0",    text)

dst_b.write_text(text)
print(f"Patched {dst_b.name}: storage paths + Setup G HL knobs (TP 1%, SL 2%, 8h hold, 5x)")

print()
print("Final HL block:")
for line in text.splitlines():
    if any(s in line for s in ("leverage:", "take_profit", "stop_loss", "time_exit_seconds",
                                "max_size_usd", "data/runtime_g", "data/audit/events_g",
                                "HYPERLIQUID")):
        print("  " + line)
