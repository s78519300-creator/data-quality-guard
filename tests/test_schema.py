from __future__ import annotations

import pytest

from data_quality_guard.datasets import Dataset
from data_quality_guard.schema import (
    detect_drift,
    infer_column_type,
    infer_type,
    is_compatible,
    parse_timestamp,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42", "integer"),
        ("-7", "integer"),
        ("3.14", "number"),
        ("1e5", "number"),
        ("true", "boolean"),
        ("2026-11-01", "date"),
        ("2026-11-01T08:12:00Z", "timestamp"),
        ("2026-11-01 08:12:00", "timestamp"),
        ("C-1031", "string"),
        (5, "integer"),
        (2.5, "number"),
        (False, "boolean"),
        ({"a": 1}, "object"),
    ],
)
def test_infer_type(value, expected):
    assert infer_type(value) == expected


def test_column_type_widens_and_ignores_nulls():
    assert infer_column_type(["1", None, "2.5"]) == "number"
    assert infer_column_type(["2026-01-01", "2026-01-02T10:00:00"]) == "timestamp"
    assert infer_column_type(["1", "x"]) == "string"
    assert infer_column_type([None, None]) == "null"


def test_compatibility_rules():
    assert is_compatible("number", "integer")
    assert is_compatible("string", "integer")  # zip codes are not drift
    assert is_compatible("integer", "null")
    assert not is_compatible("integer", "number")
    assert not is_compatible("integer", "string")


def test_parse_timestamp_normalises_to_utc():
    parsed = parse_timestamp("2026-11-01T10:00:00+02:00")
    assert parsed is not None and parsed.hour == 8 and parsed.utcoffset().total_seconds() == 0
    assert parse_timestamp("not a date") is None


def test_detect_drift_reports_added_removed_and_type_changes():
    data = Dataset(
        columns=["id", "amount", "promo"],
        rows=[{"id": "A1", "amount": "1.5", "promo": "X"}],
    )
    drift = detect_drift({"id": "integer", "amount": "number", "region": "string"}, data)
    assert drift.has_drift
    assert drift.added == {"promo": "string"}
    assert drift.removed == {"region": "string"}
    assert [(c.column, c.expected, c.observed) for c in drift.type_changed] == [
        ("id", "integer", "string")
    ]


def test_no_drift_for_matching_schema():
    data = Dataset(columns=["id"], rows=[{"id": "1"}, {"id": "2"}])
    assert not detect_drift({"id": "integer"}, data).has_drift
