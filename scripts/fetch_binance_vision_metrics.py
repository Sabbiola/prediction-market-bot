"""Bulk-download Binance Vision daily metrics zips for BTCUSDT futures.

Each zip contains a CSV with 288 rows (5-minute granularity) of:
  - sum_open_interest (USDT-margined)
  - sum_open_interest_value (USD)
  - count_toptrader_long_short_ratio
  - sum_toptrader_long_short_ratio
  - count_long_short_ratio
  - sum_taker_long_short_vol_ratio

Output: data/btc15m/binance_vision_metrics.jsonl
        (sorted by timestamp; one row per 5-min bar)
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "btc15m" / "binance_vision_metrics.jsonl"
CHECKPOINT = ROOT / "data" / "btc15m" / "binance_vision_checkpoint.json"

START = datetime(2020, 9, 1, tzinfo=UTC)


def download_day(date_str: str) -> list[dict[str, float]] | None:
    url = (
        "https://data.binance.vision/data/futures/um/daily/metrics/"
        f"BTCUSDT/BTCUSDT-metrics-{date_str}.zip"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "pmbot/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # day not available (weekend? gap?)
        raise
    zf = zipfile.ZipFile(io.BytesIO(data))
    name = zf.namelist()[0]
    text = zf.read(name).decode("utf-8")
    lines = text.strip().split("\n")
    rows: list[dict[str, float]] = []
    if len(lines) < 2:
        return rows
    headers = lines[0].split(",")
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != len(headers):
            continue
        rec: dict[str, float] = {}
        try:
            ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            rec["timestamp_ms"] = int(ts.timestamp() * 1000)
            rec["sum_open_interest"] = float(parts[2])
            rec["sum_open_interest_value"] = float(parts[3])
            rec["count_toptrader_ls"] = float(parts[4])
            rec["sum_toptrader_ls"] = float(parts[5])
            rec["count_ls_ratio"] = float(parts[6])
            rec["sum_taker_ls_vol_ratio"] = float(parts[7])
        except (ValueError, IndexError):
            continue
        rows.append(rec)
    return rows


def main() -> int:
    have_dates: set[str] = set()
    if CHECKPOINT.exists():
        try:
            cp = json.load(open(CHECKPOINT, encoding="utf-8"))
            have_dates = set(cp.get("done_dates", []))
            print(f"  resume: {len(have_dates)} days already done")
        except Exception:
            pass

    today = datetime.now(UTC).date()
    cur = START.date()
    end = today - timedelta(days=1)  # avoid today (incomplete file)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_new = 0
    n_404 = 0
    f_out = open(OUT, "a", encoding="utf-8")
    days_to_do = []
    while cur <= end:
        if cur.isoformat() not in have_dates:
            days_to_do.append(cur.isoformat())
        cur = cur + timedelta(days=1)

    print(f"  to fetch: {len(days_to_do)} days")
    try:
        for i, date_str in enumerate(days_to_do):
            try:
                rows = download_day(date_str)
            except Exception as e:
                print(f"    err {date_str}: {e}", file=sys.stderr)
                time.sleep(2.0)
                continue
            if rows is None:
                n_404 += 1
                have_dates.add(date_str)
                continue
            for r in rows:
                f_out.write(json.dumps(r) + "\n")
            n_new += len(rows)
            have_dates.add(date_str)
            if (i + 1) % 50 == 0:
                f_out.flush()
                json.dump({"done_dates": sorted(have_dates)}, open(CHECKPOINT, "w", encoding="utf-8"))
                print(
                    f"  progress {i+1}/{len(days_to_do)}  "
                    f"({date_str})  total_rows={n_new}",
                    flush=True,
                )
            time.sleep(0.05)
    finally:
        f_out.close()
        json.dump({"done_dates": sorted(have_dates)}, open(CHECKPOINT, "w", encoding="utf-8"))

    print(f"\nDONE. New rows: {n_new}, 404 days: {n_404}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
