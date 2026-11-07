from __future__ import annotations

from data_quality_guard.contract import load_contract
from data_quality_guard.datasets import Dataset, load_dataset
from data_quality_guard.engine import REASON_COLUMN, RunStatus, run_validation


def test_example_orders_are_quarantined_not_blocked(examples_dir, now):
    contract = load_contract(examples_dir / "orders_contract.json")
    result = run_validation(load_dataset(examples_dir / "orders.csv"), contract, now=now)

    assert result.status is RunStatus.QUARANTINED
    assert result.total_rows == 30
    assert len(result.quarantined_rows) == 6
    assert len(result.clean_rows) == 24
    assert abs(result.critical_rate - 0.2) < 1e-9
    reasons = {row[REASON_COLUMN] for row in result.quarantined_rows}
    assert {"order_id_present", "order_id_unique", "amount_non_negative"} <= reasons

    kinds = {(f.kind, f.column) for f in result.drift_findings}
    assert ("added", "coupon_code") in kinds
    assert ("type_changed", "customer_id") in kinds


def test_row_failing_several_checks_lists_every_reason(orders, make_contract):
    contract = make_contract(
        [
            {"type": "not_null", "column": "id"},
            {"type": "in_range", "column": "amount", "min": 0},
        ],
        quarantine={"max_critical_rate": 1.0},
    )
    result = run_validation(orders, contract)
    last = next(r for r in result.quarantined_rows if r["id"] is None)
    assert last[REASON_COLUMN] == "id_not_null;amount_in_range"


def test_warnings_do_not_quarantine(orders, make_contract):
    contract = make_contract(
        [{"type": "allowed_values", "column": "status", "values": ["new"], "severity": "warn"}]
    )
    result = run_validation(orders, contract)
    assert result.status is RunStatus.WARNED
    assert len(result.clean_rows) == len(orders)


def test_critical_rate_above_threshold_blocks(orders, make_contract):
    contract = make_contract(
        [{"type": "in_range", "column": "amount", "min": 0}],
        quarantine={"max_critical_rate": 0.25},
    )
    result = run_validation(orders, contract)
    assert result.status is RunStatus.BLOCKED
    assert "exceeds limit" in result.blocked_reasons[0]


def test_dataset_level_critical_failure_blocks(orders, make_contract):
    contract = make_contract([{"type": "row_count", "min": 100}])
    result = run_validation(orders, contract)
    assert result.status is RunStatus.BLOCKED
    assert not result.quarantined_rows  # nothing to quarantine row-by-row


def test_removed_column_is_critical_by_default(make_contract):
    data = Dataset(columns=["id"], rows=[{"id": "1"}])
    contract = make_contract([], schema={"columns": {"id": "integer", "region": "string"}})
    result = run_validation(data, contract)
    assert result.status is RunStatus.BLOCKED
    assert "region" in result.blocked_reasons[0]


def test_clean_batch_passes(make_contract):
    data = Dataset(columns=["id"], rows=[{"id": "1"}, {"id": "2"}])
    contract = make_contract(
        [{"type": "unique", "column": "id"}], schema={"columns": {"id": "integer"}}
    )
    result = run_validation(data, contract)
    assert result.status is RunStatus.PASSED
    assert result.highest_failed_severity() is None
    assert result.summary()["checks_passed"] == 1
