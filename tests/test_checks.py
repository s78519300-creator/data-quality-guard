from __future__ import annotations

from datetime import timedelta

import pytest

from data_quality_guard.checks import CheckContext, run_check
from data_quality_guard.datasets import Dataset


def _run(dataset, contract, now=None):
    ctx = CheckContext(contract=contract, now=now) if now else CheckContext(contract=contract)
    return {spec.name: run_check(dataset, spec, ctx) for spec in contract.checks}


def test_not_null_flags_missing_values(orders, make_contract):
    contract = make_contract([{"type": "not_null", "column": "id"}])
    result = _run(orders, contract)["id_not_null"]
    assert not result.passed
    assert result.failed_rows == [3]


def test_unique_keeps_first_occurrence_and_ignores_nulls(orders, make_contract):
    contract = make_contract([{"type": "unique", "column": "id"}])
    result = _run(orders, contract)["id_unique"]
    assert result.failed_rows == [2]
    assert result.samples == ["2"]


def test_unique_treats_int_and_string_forms_as_equal(make_contract):
    data = Dataset.from_rows([{"k": 1}, {"k": "1"}, {"k": 1.0}])
    result = _run(data, make_contract([{"type": "unique", "column": "k"}]))["k_unique"]
    assert result.failed_rows == [1, 2]


def test_in_range_rejects_out_of_bounds_and_non_numeric(orders, make_contract):
    contract = make_contract([{"type": "in_range", "column": "amount", "min": 0}])
    result = _run(orders, contract)["amount_in_range"]
    assert result.failed_rows == [1, 3]


def test_allowed_values_case_insensitive_option(make_contract):
    data = Dataset.from_rows([{"c": "usd"}, {"c": "EUR"}, {"c": "JPY"}])
    contract = make_contract(
        [{"type": "allowed_values", "column": "c", "values": ["USD", "EUR"],
          "case_sensitive": False}]
    )
    assert _run(data, contract)["c_allowed_values"].failed_rows == [2]


def test_regex_uses_full_match(orders, make_contract):
    contract = make_contract(
        [{"type": "regex", "column": "email", "pattern": r"[^@\s]+@[^@\s]+\.[a-z]+"}]
    )
    result = _run(orders, contract)["email_regex"]
    assert result.failed_rows == [3]  # the null email is not this check's business


def test_type_check_on_string_values(make_contract):
    data = Dataset(columns=["q"], rows=[{"q": "1"}, {"q": "2.5"}, {"q": "two"}, {"q": None}])
    contract = make_contract([{"type": "type", "column": "q", "expected": "integer"}])
    assert _run(data, contract)["q_type"].failed_rows == [1, 2]


def test_referential_check_against_csv(tmp_path, make_contract):
    (tmp_path / "customers.csv").write_text("customer_id,name\n1,Ann\n2,Bob\n")
    data = Dataset(columns=["customer_id"], rows=[{"customer_id": v} for v in ["1", "3", "2"]])
    contract = make_contract(
        [{"type": "referential", "column": "customer_id", "dataset": "customers.csv",
          "ref_column": "customer_id"}]
    )
    assert _run(data, contract)["customer_id_referential"].failed_rows == [1]


def test_referential_check_missing_reference_column_raises(tmp_path, make_contract):
    (tmp_path / "ref.csv").write_text("other\n1\n")
    data = Dataset(columns=["k"], rows=[{"k": "1"}])
    contract = make_contract(
        [{"type": "referential", "column": "k", "dataset": "ref.csv", "ref_column": "k"}]
    )
    with pytest.raises(ValueError, match="no column 'k'"):
        _run(data, contract)


@pytest.mark.parametrize(("hours_old", "passes"), [(1, True), (47.9, True), (49, False)])
def test_freshness(now, make_contract, hours_old, passes):
    newest = (now - timedelta(hours=hours_old)).isoformat()
    data = Dataset(columns=["ts"], rows=[{"ts": "2020-01-01T00:00:00Z"}, {"ts": newest}])
    contract = make_contract([{"type": "freshness", "column": "ts", "max_age_hours": 48}])
    result = _run(data, contract, now=now)["ts_freshness"]
    assert result.passed is passes
    assert result.row_level is False


def test_freshness_without_timestamps_fails(now, make_contract):
    data = Dataset(columns=["ts"], rows=[{"ts": "yesterday"}])
    contract = make_contract([{"type": "freshness", "column": "ts", "max_age_hours": 1}])
    assert "no parseable" in _run(data, contract, now=now)["ts_freshness"].message


def test_row_count_bounds(orders, make_contract):
    contract = make_contract(
        [
            {"name": "enough", "type": "row_count", "min": 2},
            {"name": "too_many", "type": "row_count", "max": 3},
        ]
    )
    results = _run(orders, contract)
    assert results["enough"].passed
    assert not results["too_many"].passed


def test_missing_column_fails_every_row(orders, make_contract):
    contract = make_contract([{"type": "not_null", "column": "nope"}])
    result = _run(orders, contract)["nope_not_null"]
    assert not result.passed
    assert result.failed_count == len(orders)
    assert "missing" in result.message
