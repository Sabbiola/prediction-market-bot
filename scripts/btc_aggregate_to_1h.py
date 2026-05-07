"""Aggregate BTC 15m JSONL OHLCV → 1h candles.

Reads data/btc/ohlcv_15m.jsonl + data/btc/coinbase_15m.jsonl and emits
data/btc/ohlcv_1h.jsonl + data/btc/coinbase_1h.jsonl.  A 1h bar is built
only when all 4 of its 15m components are present (no partial hours).

Used as the input data for `btc_train_v2.py --interval 1h`.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def aggregate(in_path: Path, out_path: Path) -> int:
    if not in_path.exists():
        print(f"SKIP {in_path}: missing")
        return 0
    buckets: dict[int, list[dict]] = defaultdict(list)
    for line in in_path.open():
        try:
            c = json.loads(line)
        except Exception:
            continue
        ts = int(c["open_time_ms"])
        # Bucket by floor of ts (in ms) to nearest hour.
        hour_bucket = (ts // 3_600_000) * 3_600_000
        buckets[hour_bucket].append(c)

    rows: list[dict] = []
    for hour_ts in sorted(buckets):
        bs = buckets[hour_ts]
        if len(bs) < 4:
            # Skip incomplete hours so OHLC is consistent with full 1h bars
            continue
        bs.sort(key=lambda c: c["open_time_ms"])
        rows.append({
            "open_time_ms": hour_ts,
            "open":   float(bs[0]["open"]),
            "high":   max(float(c["high"]) for c in bs),
            "low":    min(float(c["low"])  for c in bs),
            "close":  float(bs[-1]["close"]),
            "volume": sum(float(c["volume"]) for c in bs),
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r))
            f.write("\n")
    if rows:
        print(f"DONE {out_path.name}: {len(rows)} 1h candles  "
              f"(first={rows[0]['open_time_ms']}, last={rows[-1]['open_time_ms']})")
    else:
        print(f"DONE {out_path.name}: 0 rows")
    return len(rows)


def main() -> int:
    base = Path("/opt/pmbot/data/btc") if Path("/opt/pmbot").exists() else Path("data/btc")
    a = aggregate(base / "ohlcv_15m.jsonl",   base / "ohlcv_1h.jsonl")
    b = aggregate(base / "coinbase_15m.jsonl", base / "coinbase_1h.jsonl")
    return 0 if (a > 0 or b > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
