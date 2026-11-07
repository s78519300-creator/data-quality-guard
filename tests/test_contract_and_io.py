from __future__ import annotations

import json

import pytest

from data_quality_guard.contract import ContractError, Severity, load_contract, parse_contract
from data_quality_guard.datasets import DatasetError, load_dataset, write_rows


def test_example_contract_loads(examples_dir):
    contract = load_contract(examples_dir / "orders_contract.json")
    assert contract.dataset == "orders"
    assert contract.max_critical_rate == 0.25
    assert contract.schema is not None
    assert contract.schema.drift_severity["removed"] is Severity.CRITICAL
    assert {c.type for c in contract.checks} >= {"not_null", "unique", "freshness"}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"checks": [{"type": "bogus", "column": "a"}]}, "unknown check type"),
        ({"checks": [{"type": "not_null"}]}, "requires a 'column'"),
        ({"checks": [{"type": "regex", "column": "a"}]}, "missing: pattern"),
        ({"checks": [{"type": "not_null", "column": "a", "severity": "fatal"}]}, "severity"),
        ({"checks": [{"type": "in_range", "column": "a"}]}, "at least one"),
        (
            {"checks": [{"type": "not_null", "column": "a"}, {"type": "not_null", "column": "a"}]},
            "Duplicate",
        ),
        ({"quarantine": {"max_critical_rate": 2}}, "between 0 and 1"),
        ({"schema": {"columns": {"a": "varchar"}}}, "unknown type"),
    ],
)
def test_invalid_contracts_are_rejected(raw, message):
    with pytest.raises(ContractError, match=message):
        parse_contract(raw)


def test_contract_with_bad_json(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    with pytest.raises(ContractError, match="invalid JSON"):
        load_contract(path)


def test_csv_empty_cells_become_null(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,\n ,x\n")
    data = load_dataset(path)
    assert data.columns == ["a", "b"]
    assert data.rows == [{"a": "1", "b": None}, {"a": None, "b": "x"}]
    assert data.name == "d"


def test_jsonl_round_trip(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"id": 1, "tag": "a"}\n\n{"id": 2, "extra": true}\n')
    data = load_dataset(path)
    assert data.columns == ["id", "tag", "extra"]
    out = write_rows(tmp_path / "copy.jsonl", data.columns, data.rows, "jsonl")
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines[1] == {"id": 2, "tag": None, "extra": True}


def test_unsupported_and_invalid_files(tmp_path):
    (tmp_path / "x.parquet").write_text("")
    with pytest.raises(DatasetError, match="Unsupported"):
        load_dataset(tmp_path / "x.parquet")
    (tmp_path / "bad.jsonl").write_text("[1, 2]\n")
    with pytest.raises(DatasetError, match="JSON object"):
        load_dataset(tmp_path / "bad.jsonl")
    with pytest.raises(DatasetError, match="not found"):
        load_dataset(tmp_path / "missing.csv")
