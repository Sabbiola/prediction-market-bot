"""Build a per-market dataset combining each bot's prediction + outcome.

For every Polymarket BTC 15m market that has been resolved by *any* bot, we
emit one row with:
  - the runtime features captured at decision time (BTC return/RSI/CB/etc.)
  - each model's fair_yes_prob / edge / confidence (NaN if that model didn't see it)
  - the binary outcome resolved_yes (label)

Output:
  data/meta/btc_15m_meta_train.csv
  data/meta/btc_15m_meta_summary.json

This dataset is the input for ``train_meta_ensemble.py`` which fits a
LightGBM stacker over the 5 model probabilities + raw features.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

# (label, audit_log, runtime_features_artifact)
SOURCES: list[tuple[str, str, str]] = [
    ("v4",  "data/audit/events.jsonl",     ""),  # v4 has no artifacts dir
    ("v5",  "data/audit/events_v5.jsonl",  "data/artifacts_v5/prediction_runtime_features.jsonl"),
    ("v6",  "data/audit/events_v6.jsonl",  "data/artifacts_v6/prediction_runtime_features.jsonl"),
    ("llm", "data/audit/events_llm.jsonl", "data/artifacts_llm/prediction_runtime_features.jsonl"),
    ("e",   "data/audit/events_e.jsonl",   "data/artifacts_e/prediction_runtime_features.jsonl"),
]


def read_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def parse_audit(path: Path):
    """Return per-market dict: {market_id: {"pred":(prob,edge,conf,side), "outcome":bool, "fill":(side,stake,price)}}."""
    out: dict[str, dict] = {}
    for e in read_jsonl(path):
        et = e.get("event_type", "")
        p = e.get("payload", {})
        mid = str(p.get("market_id", ""))
        if not mid:
            continue
        rec = out.setdefault(mid, {})
        if et == "prediction_end":
            rec["pred"] = (
                p.get("fair_yes_prob"),
                p.get("edge"),
                p.get("confidence"),
                p.get("selected_side", ""),
            )
        elif et == "paper_portfolio_fill":
            rec["fill"] = (
                p.get("side", ""),
                float(p.get("filled_stake_usd", 0) or 0),
                float(p.get("fill_price", 0) or 0),
            )
        elif et == "paper_portfolio_settle":
            ry = p.get("resolved_yes")
            if ry is not None:
                rec["outcome"] = bool(ry)
    return out


def parse_runtime_features(path: Path):
    """Return {market_id: features_dict}."""
    out: dict[str, dict] = {}
    for e in read_jsonl(path):
        p = e.get("payload", {})
        mid = str(p.get("market_id", ""))
        feats = p.get("values") or p.get("features") or p.get("feature_values") or {}
        if mid and isinstance(feats, dict) and feats:
            out[mid] = feats
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("/opt/pmbot"))
    ap.add_argument("--out-csv",     type=Path, default=Path("data/meta/btc_15m_meta_train.csv"))
    ap.add_argument("--out-summary", type=Path, default=Path("data/meta/btc_15m_meta_summary.json"))
    args = ap.parse_args()

    root = args.root.resolve()
    out_csv = (root / args.out_csv).resolve()
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_summary = (root / args.out_summary).resolve()

    print(f"Loading audit logs and feature artifacts from {root} ...")
    bots: dict[str, dict] = {}
    feature_index: dict[str, dict[str, dict]] = {}
    for label, audit_rel, feat_rel in SOURCES:
        bots[label] = parse_audit(root / audit_rel)
        feature_index[label] = parse_runtime_features(root / feat_rel) if feat_rel else {}
        print(f"  {label:>4}: {len(bots[label]):>5} markets seen  "
              f"({sum(1 for r in bots[label].values() if 'outcome' in r)} resolved, "
              f"{len(feature_index[label])} feature snapshots)")

    # Union of all market_ids that have a known outcome from at least one bot
    market_outcomes: dict[str, bool] = {}
    for rec_by_mid in bots.values():
        for mid, rec in rec_by_mid.items():
            if "outcome" in rec and mid not in market_outcomes:
                market_outcomes[mid] = rec["outcome"]
    print(f"Unique markets with outcome: {len(market_outcomes)}")

    # Pick the feature snapshot from the bot that ran first / has features for that market.
    # Priority: v5 → v6 → e → llm → v4 (v4 has no feature artifacts).
    priority = ["v5", "v6", "e", "llm", "v4"]

    rows: list[dict] = []
    feature_keys: set[str] = set()
    for mid, ry in market_outcomes.items():
        feats: dict | None = None
        for lbl in priority:
            if mid in feature_index.get(lbl, {}):
                feats = feature_index[lbl][mid]
                break
        if feats is None:
            continue
        feature_keys.update(feats.keys())

        row: dict = {
            "market_id": mid,
            "resolved_yes": int(ry),
        }
        for lbl, rec_by_mid in bots.items():
            rec = rec_by_mid.get(mid, {})
            pred = rec.get("pred")
            if pred:
                prob, edge, conf, side = pred
                row[f"pred_{lbl}_prob"] = prob if prob is not None else ""
                row[f"pred_{lbl}_edge"] = edge if edge is not None else ""
                row[f"pred_{lbl}_conf"] = conf if conf is not None else ""
                row[f"pred_{lbl}_side"] = side or ""
            else:
                row[f"pred_{lbl}_prob"] = ""
                row[f"pred_{lbl}_edge"] = ""
                row[f"pred_{lbl}_conf"] = ""
                row[f"pred_{lbl}_side"] = ""
        # Numeric features only
        for k, v in feats.items():
            if isinstance(v, (int, float)) and not (isinstance(v, bool)):
                row[f"feat_{k}"] = v
        rows.append(row)

    if not rows:
        print("No usable rows — nothing to write.")
        return 1

    feat_cols = sorted({c for r in rows for c in r if c.startswith("feat_")})
    pred_cols = []
    for lbl in [s[0] for s in SOURCES]:
        for k in ("prob", "edge", "conf", "side"):
            pred_cols.append(f"pred_{lbl}_{k}")
    cols = ["market_id", "resolved_yes"] + pred_cols + feat_cols

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    summary = {
        "rows": len(rows),
        "feature_columns": len(feat_cols),
        "model_columns": len(pred_cols),
        "yes_rate": sum(r["resolved_yes"] for r in rows) / len(rows),
        "per_model_coverage": {
            lbl: sum(1 for r in rows if r.get(f"pred_{lbl}_prob") not in (None, "")) for lbl in [s[0] for s in SOURCES]
        },
        "csv": str(out_csv),
    }
    out_summary.write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {len(rows)} rows × {len(cols)} cols → {out_csv}")
    print(json.dumps(summary["per_model_coverage"], indent=2))
    print(f"yes_rate: {summary['yes_rate']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
