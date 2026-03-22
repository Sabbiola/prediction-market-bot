from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from .models import HoldoutSplits, SplitRows, TrainingFeatureRow


def build_holdout_splits(
    rows: Sequence[TrainingFeatureRow],
    *,
    train_ratio: float,
    validation_ratio: float,
) -> HoldoutSplits:
    ordered = tuple(sorted(rows, key=lambda row: (row.decision_timestamp_utc, row.market_id, row.row_id)))
    n = len(ordered)
    if n == 0:
        return HoldoutSplits(
            train=SplitRows("train", ()),
            validation=SplitRows("validation", ()),
            test=SplitRows("test", ()),
        )
    if n == 1:
        return HoldoutSplits(
            train=SplitRows("train", ordered),
            validation=SplitRows("validation", ()),
            test=SplitRows("test", ()),
        )
    if n == 2:
        return HoldoutSplits(
            train=SplitRows("train", ordered[:1]),
            validation=SplitRows("validation", ()),
            test=SplitRows("test", ordered[1:]),
        )

    safe_train_ratio = min(max(train_ratio, 0.05), 0.90)
    safe_validation_ratio = min(max(validation_ratio, 0.05), 0.90)
    train_count = int(round(n * safe_train_ratio))
    train_count = min(max(train_count, 1), n - 2)
    validation_count = int(round(n * safe_validation_ratio))
    validation_count = min(max(validation_count, 1), n - train_count - 1)
    test_count = n - train_count - validation_count
    if test_count <= 0:
        test_count = 1
        if validation_count > 1:
            validation_count -= 1
        else:
            train_count = max(train_count - 1, 1)

    return HoldoutSplits(
        train=SplitRows("train", ordered[:train_count]),
        validation=SplitRows("validation", ordered[train_count : train_count + validation_count]),
        test=SplitRows("test", ordered[train_count + validation_count :]),
    )


def temporal_leakage_errors(splits: HoldoutSplits) -> list[str]:
    errors: list[str] = []
    train_ids = {row.row_id for row in splits.train.rows}
    validation_ids = {row.row_id for row in splits.validation.rows}
    test_ids = {row.row_id for row in splits.test.rows}
    if train_ids & validation_ids:
        errors.append("train/validation overlap rows")
    if train_ids & test_ids:
        errors.append("train/test overlap rows")
    if validation_ids & test_ids:
        errors.append("validation/test overlap rows")
    if splits.train.rows and splits.validation.rows:
        train_max = max(row.decision_timestamp_utc for row in splits.train.rows)
        validation_min = min(row.decision_timestamp_utc for row in splits.validation.rows)
        if train_max > validation_min:
            errors.append("train timestamp exceeds validation start")
    if splits.validation.rows and splits.test.rows:
        validation_max = max(row.decision_timestamp_utc for row in splits.validation.rows)
        test_min = min(row.decision_timestamp_utc for row in splits.test.rows)
        if validation_max > test_min:
            errors.append("validation timestamp exceeds test start")
    return errors


def month_window_key(row: TrainingFeatureRow) -> str:
    return row.decision_timestamp_utc.strftime("%Y-%m")


def rolling_windows(
    rows: Sequence[TrainingFeatureRow],
    *,
    window_days: int,
) -> dict[str, tuple[TrainingFeatureRow, ...]]:
    ordered = tuple(sorted(rows, key=lambda row: row.decision_timestamp_utc))
    if not ordered:
        return {}
    size_days = max(window_days, 1)
    start = ordered[0].decision_timestamp_utc
    end = ordered[-1].decision_timestamp_utc
    windows: dict[str, tuple[TrainingFeatureRow, ...]] = {}
    cursor = start
    index = 1
    while cursor <= end:
        window_end = cursor + timedelta(days=size_days)
        subset = tuple(row for row in ordered if cursor <= row.decision_timestamp_utc < window_end)
        if subset:
            label = f"window_{index:03d}_{cursor.date().isoformat()}"
            windows[label] = subset
        cursor = window_end
        index += 1
    return windows
