"""Type inference and schema drift detection.

CSV files carry no type information, so types are inferred from the values
themselves. The inferred vocabulary is small on purpose:

``integer``, ``number``, ``boolean``, ``date``, ``timestamp``, ``string``,
plus ``object``/``array`` for nested JSONL values and ``null`` for a column
that contained no values at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from data_quality_guard.datasets import Dataset

KNOWN_TYPES = (
    "integer",
    "number",
    "boolean",
    "date",
    "timestamp",
    "string",
    "object",
    "array",
)

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_BOOL_VALUES = {"true", "false"}


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 date or datetime into an aware UTC ``datetime``.

    Returns ``None`` when the value cannot be parsed. Naive timestamps are
    assumed to be UTC, which matches how most ETL extracts are written.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def infer_type(value: Any) -> str:
    """Infer the logical type of a single non-null value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    text = str(value).strip()
    if text.lower() in _BOOL_VALUES:
        return "boolean"
    if _INT_RE.match(text):
        return "integer"
    if _NUM_RE.match(text):
        return "number"
    if _DATE_RE.match(text) and parse_timestamp(text) is not None:
        return "date"
    if _TS_RE.match(text) and parse_timestamp(text) is not None:
        return "timestamp"
    return "string"


def merge_types(types: set[str]) -> str:
    """Collapse the set of per-value types seen in a column into one type."""
    if not types:
        return "null"
    if len(types) == 1:
        return next(iter(types))
    if types <= {"integer", "number"}:
        return "number"
    if types <= {"date", "timestamp"}:
        return "timestamp"
    return "string"


def infer_column_type(values: list[Any]) -> str:
    """Infer the type of a whole column, ignoring nulls."""
    return merge_types({infer_type(v) for v in values if v is not None})


def infer_schema(dataset: Dataset) -> dict[str, str]:
    """Return ``{column: inferred_type}`` in source column order."""
    return {col: infer_column_type(dataset.values(col)) for col in dataset.columns}


def is_compatible(expected: str, observed: str) -> bool:
    """Return True when data of type ``observed`` satisfies ``expected``.

    The rules are lenient where widening is harmless: an all-null column fits
    any type, integers fit ``number``, dates fit ``timestamp`` and anything
    scalar fits ``string`` (a string column that happens to hold only digits,
    such as a zip code, is not drift).
    """
    if observed == "null" or expected == observed:
        return True
    if expected == "number" and observed == "integer":
        return True
    if expected == "timestamp" and observed == "date":
        return True
    if expected == "string" and observed not in ("object", "array"):
        return True
    return False


def value_matches_type(value: Any, expected: str) -> bool:
    """Row-level type check used by the ``type`` check."""
    if value is None:
        return True
    return is_compatible(expected, infer_type(value))


@dataclass
class TypeChange:
    """A column whose observed type no longer matches the contract."""

    column: str
    expected: str
    observed: str


@dataclass
class DriftReport:
    """Differences between the expected schema and what actually arrived."""

    added: dict[str, str] = field(default_factory=dict)
    removed: dict[str, str] = field(default_factory=dict)
    type_changed: list[TypeChange] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.type_changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "added": dict(self.added),
            "removed": dict(self.removed),
            "type_changed": [
                {"column": c.column, "expected": c.expected, "observed": c.observed}
                for c in self.type_changed
            ],
        }


def detect_drift(expected: dict[str, str], dataset: Dataset) -> DriftReport:
    """Compare a dataset against the expected ``{column: type}`` mapping."""
    observed = infer_schema(dataset)
    report = DriftReport()
    for column, col_type in observed.items():
        if column not in expected:
            report.added[column] = col_type
    for column, exp_type in expected.items():
        if column not in observed:
            report.removed[column] = exp_type
            continue
        if not is_compatible(exp_type, observed[column]):
            report.type_changed.append(TypeChange(column, exp_type, observed[column]))
    return report
