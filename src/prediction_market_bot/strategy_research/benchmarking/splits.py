from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from .models import LabelRecord


@dataclass(slots=True, frozen=True)
class SplitSlice:
    name: str
    rows: tuple[LabelRecord, ...]

    @property
    def size(self) -> int:
        return len(self.rows)


@dataclass(slots=True, frozen=True)
class TemporalFold:
    fold_index: int
    train: SplitSlice
    validation: SplitSlice
    test: SplitSlice


def build_holdout_fold(
    rows: Sequence[LabelRecord],
    *,
    train_ratio: float,
    validation_ratio: float,
) -> TemporalFold:
    ordered = tuple(sorted(rows, key=lambda row: (row.decision_timestamp_utc, row.market_id)))
    n = len(ordered)
    if n == 0:
        return TemporalFold(
            fold_index=1,
            train=SplitSlice("train", ()),
            validation=SplitSlice("validation", ()),
            test=SplitSlice("test", ()),
        )
    if n == 1:
        return TemporalFold(
            fold_index=1,
            train=SplitSlice("train", ordered),
            validation=SplitSlice("validation", ()),
            test=SplitSlice("test", ()),
        )
    if n == 2:
        return TemporalFold(
            fold_index=1,
            train=SplitSlice("train", ordered[:1]),
            validation=SplitSlice("validation", ()),
            test=SplitSlice("test", ordered[1:]),
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

    train_rows = ordered[:train_count]
    validation_rows = ordered[train_count : train_count + validation_count]
    test_rows = ordered[train_count + validation_count :]
    return TemporalFold(
        fold_index=1,
        train=SplitSlice("train", train_rows),
        validation=SplitSlice("validation", validation_rows),
        test=SplitSlice("test", test_rows),
    )


def build_walk_forward_folds(
    rows: Sequence[LabelRecord],
    *,
    train_days: int,
    validation_days: int,
    test_days: int,
    step_days: int,
    max_folds: int,
) -> list[TemporalFold]:
    ordered = tuple(sorted(rows, key=lambda row: (row.decision_timestamp_utc, row.market_id)))
    if not ordered:
        return []

    train_days = max(train_days, 1)
    validation_days = max(validation_days, 1)
    test_days = max(test_days, 1)
    step_days = max(step_days, 1)
    max_folds = max(max_folds, 0)

    first_ts = ordered[0].decision_timestamp_utc
    last_ts = ordered[-1].decision_timestamp_utc
    folds: list[TemporalFold] = []
    fold_idx = 1
    step_idx = 0
    while True:
        train_end = first_ts + timedelta(days=train_days + (step_idx * step_days))
        validation_end = train_end + timedelta(days=validation_days)
        test_end = validation_end + timedelta(days=test_days)

        train_rows = tuple(row for row in ordered if row.decision_timestamp_utc < train_end)
        validation_rows = tuple(
            row for row in ordered if train_end <= row.decision_timestamp_utc < validation_end
        )
        test_rows = tuple(
            row for row in ordered if validation_end <= row.decision_timestamp_utc < test_end
        )

        if not train_rows or not validation_rows or not test_rows:
            if validation_end > last_ts:
                break
            step_idx += 1
            if train_end > last_ts:
                break
            continue

        folds.append(
            TemporalFold(
                fold_index=fold_idx,
                train=SplitSlice("train", train_rows),
                validation=SplitSlice("validation", validation_rows),
                test=SplitSlice("test", test_rows),
            )
        )
        fold_idx += 1
        step_idx += 1

        if max_folds > 0 and len(folds) >= max_folds:
            break
        if validation_end > last_ts:
            break
    return folds


def temporal_leakage_errors(fold: TemporalFold) -> list[str]:
    errors: list[str] = []
    train_ids = {row.row_id for row in fold.train.rows}
    validation_ids = {row.row_id for row in fold.validation.rows}
    test_ids = {row.row_id for row in fold.test.rows}

    if train_ids & validation_ids:
        errors.append(f"fold={fold.fold_index}: train/validation overlap rows")
    if train_ids & test_ids:
        errors.append(f"fold={fold.fold_index}: train/test overlap rows")
    if validation_ids & test_ids:
        errors.append(f"fold={fold.fold_index}: validation/test overlap rows")

    if fold.train.rows and fold.validation.rows:
        train_max = max(row.decision_timestamp_utc for row in fold.train.rows)
        validation_min = min(row.decision_timestamp_utc for row in fold.validation.rows)
        if train_max > validation_min:
            errors.append(f"fold={fold.fold_index}: train timestamp exceeds validation start")

    if fold.validation.rows and fold.test.rows:
        validation_max = max(row.decision_timestamp_utc for row in fold.validation.rows)
        test_min = min(row.decision_timestamp_utc for row in fold.test.rows)
        if validation_max > test_min:
            errors.append(f"fold={fold.fold_index}: validation timestamp exceeds test start")
    return errors
