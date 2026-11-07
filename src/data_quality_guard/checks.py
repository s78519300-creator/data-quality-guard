"""Check implementations.

Each check is a plain function ``(dataset, spec, context) -> CheckResult``
registered in :data:`CHECKS`. Row-level checks return the indices of the rows
that failed so the engine can quarantine them; dataset-level checks
(``row_count``, ``freshness``) fail or pass the batch as a whole.

Null handling is consistent: every check except ``not_null`` ignores nulls,
so "is it present?" and "is it valid?" stay separate concerns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from data_quality_guard.contract import CheckSpec, Contract, Severity
from data_quality_guard.datasets import Dataset, load_dataset
from data_quality_guard.schema import parse_timestamp, value_matches_type

MAX_SAMPLES = 5


@dataclass
class CheckContext:
    """Run-wide information a check may need."""

    contract: Contract
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _reference_cache: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    def reference_values(self, path: str, column: str) -> set[str]:
        """Load (and cache) the distinct values of a column in another dataset."""
        key = (path, column)
        if key not in self._reference_cache:
            ref = load_dataset(self.contract.resolve(path))
            if column not in ref.columns:
                raise ValueError(f"reference dataset {path} has no column '{column}'")
            self._reference_cache[key] = {
                _key(v) for v in ref.values(column) if v is not None
            }
        return self._reference_cache[key]


@dataclass
class CheckResult:
    """Outcome of a single check."""

    name: str
    check_type: str
    column: Optional[str]
    severity: Severity
    passed: bool
    message: str
    failed_rows: list[int] = field(default_factory=list)
    samples: list[Any] = field(default_factory=list)
    row_level: bool = True

    @property
    def failed_count(self) -> int:
        return len(self.failed_rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.check_type,
            "column": self.column,
            "severity": self.severity.value,
            "passed": self.passed,
            "failed_count": self.failed_count,
            "failed_rows": [i + 1 for i in self.failed_rows[:50]],
            "samples": self.samples,
            "message": self.message,
        }


CheckFn = Callable[[Dataset, CheckSpec, CheckContext], CheckResult]


def _key(value: Any) -> str:
    """Normalise a value for set membership so that 7, "7" and 7.0 compare equal."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_result(
    spec: CheckSpec, dataset: Dataset, failed: list[int], what: str
) -> CheckResult:
    """Build a result for a row-level check from the failing indices."""
    samples: list[Any] = []
    for i in failed:
        value = dataset.rows[i].get(spec.column) if spec.column else None
        if value not in samples:
            samples.append(value)
        if len(samples) >= MAX_SAMPLES:
            break
    if failed:
        message = f"{len(failed)} of {len(dataset)} rows {what}"
    else:
        message = f"all {len(dataset)} rows ok"
    return CheckResult(
        name=spec.name,
        check_type=spec.type,
        column=spec.column,
        severity=spec.severity,
        passed=not failed,
        message=message,
        failed_rows=failed,
        samples=samples,
    )


def _missing_column(spec: CheckSpec, dataset: Dataset) -> Optional[CheckResult]:
    """Fail every row if the column a check depends on is not in the data."""
    if spec.column and spec.column not in dataset.columns:
        return CheckResult(
            name=spec.name,
            check_type=spec.type,
            column=spec.column,
            severity=spec.severity,
            passed=False,
            message=f"column '{spec.column}' is missing from the data",
            failed_rows=list(range(len(dataset))),
        )
    return None


def check_not_null(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    failed = [i for i, row in enumerate(dataset.rows) if row.get(spec.column) is None]
    return _row_result(spec, dataset, failed, "are null")


def check_unique(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    """Flag repeated values. The first occurrence is kept as the valid one."""
    seen: set[str] = set()
    failed: list[int] = []
    for i, row in enumerate(dataset.rows):
        value = row.get(spec.column)
        if value is None:
            continue
        key = _key(value)
        if key in seen:
            failed.append(i)
        else:
            seen.add(key)
    return _row_result(spec, dataset, failed, "are duplicates")


def check_in_range(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    low = spec.params.get("min")
    high = spec.params.get("max")
    failed: list[int] = []
    for i, row in enumerate(dataset.rows):
        value = row.get(spec.column)
        if value is None:
            continue
        number = _to_number(value)
        if number is None or (low is not None and number < low) or (
            high is not None and number > high
        ):
            failed.append(i)
    bounds = f"[{'-inf' if low is None else low}, {'inf' if high is None else high}]"
    return _row_result(spec, dataset, failed, f"are outside {bounds}")


def check_allowed_values(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    case_sensitive = spec.params.get("case_sensitive", True)
    allowed = {_key(v) if case_sensitive else _key(v).lower() for v in spec.params["values"]}
    failed: list[int] = []
    for i, row in enumerate(dataset.rows):
        value = row.get(spec.column)
        if value is None:
            continue
        key = _key(value) if case_sensitive else _key(value).lower()
        if key not in allowed:
            failed.append(i)
    return _row_result(spec, dataset, failed, "have values outside the allowed set")


def check_regex(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    pattern = re.compile(spec.params["pattern"])
    failed = [
        i
        for i, row in enumerate(dataset.rows)
        if row.get(spec.column) is not None
        and not pattern.fullmatch(str(row.get(spec.column)))
    ]
    return _row_result(spec, dataset, failed, "do not match the pattern")


def check_type(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    expected = spec.params["expected"]
    failed = [
        i
        for i, row in enumerate(dataset.rows)
        if not value_matches_type(row.get(spec.column), expected)
    ]
    return _row_result(spec, dataset, failed, f"are not of type {expected}")


def check_referential(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    known = ctx.reference_values(spec.params["dataset"], spec.params["ref_column"])
    failed = [
        i
        for i, row in enumerate(dataset.rows)
        if row.get(spec.column) is not None and _key(row.get(spec.column)) not in known
    ]
    target = f"{spec.params['dataset']}[{spec.params['ref_column']}]"
    return _row_result(spec, dataset, failed, f"have no match in {target}")


def check_freshness(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    """Pass when the newest timestamp in the column is younger than ``max_age_hours``."""
    max_age = float(spec.params["max_age_hours"])
    stamps = [t for t in (parse_timestamp(v) for v in dataset.values(spec.column)) if t]
    result = CheckResult(
        name=spec.name,
        check_type=spec.type,
        column=spec.column,
        severity=spec.severity,
        passed=False,
        message="",
        row_level=False,
    )
    if not stamps:
        result.message = "no parseable timestamps found"
        return result
    newest = max(stamps)
    age_hours = (ctx.now - newest).total_seconds() / 3600
    result.samples = [newest.isoformat()]
    result.passed = age_hours <= max_age
    verdict = "within" if result.passed else "exceeds"
    result.message = f"newest value is {age_hours:.1f}h old ({verdict} {max_age:g}h limit)"
    return result


def check_row_count(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    low = spec.params.get("min")
    high = spec.params.get("max")
    count = len(dataset)
    passed = (low is None or count >= low) and (high is None or count <= high)
    bounds = f"[{low if low is not None else 0}, {high if high is not None else 'inf'}]"
    return CheckResult(
        name=spec.name,
        check_type=spec.type,
        column=None,
        severity=spec.severity,
        passed=passed,
        message=f"{count} rows ({'within' if passed else 'outside'} {bounds})",
        row_level=False,
    )


CHECKS: dict[str, CheckFn] = {
    "not_null": check_not_null,
    "unique": check_unique,
    "in_range": check_in_range,
    "allowed_values": check_allowed_values,
    "regex": check_regex,
    "type": check_type,
    "referential": check_referential,
    "freshness": check_freshness,
    "row_count": check_row_count,
}


def run_check(dataset: Dataset, spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    """Run one check, turning a missing column into a failed result."""
    missing = _missing_column(spec, dataset)
    if missing is not None:
        missing.row_level = spec.type not in ("freshness", "row_count")
        if not missing.row_level:
            missing.failed_rows = []
        return missing
    return CHECKS[spec.type](dataset, spec, ctx)
