from __future__ import annotations

import csv
import io
import json
import subprocess
import sys

from data_quality_guard.cli import EXIT_BLOCKED, EXIT_ERROR, EXIT_OK, main


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _validate(examples_dir, out_dir, *extra):
    return _run(
        [
            "validate",
            "--data", str(examples_dir / "orders.csv"),
            "--contract", str(examples_dir / "orders_contract.json"),
            "--out", str(out_dir),
            "--now", "2026-11-08T09:00:00Z",
            *extra,
        ]
    )


def test_validate_example_writes_all_outputs(examples_dir, tmp_path):
    code, out, err = _validate(examples_dir, tmp_path)
    assert code == EXIT_OK
    assert "Result: QUARANTINED" in out
    assert "ALERT CRITICAL" in err

    with (tmp_path / "orders_quarantine.csv").open() as handle:
        quarantined = list(csv.DictReader(handle))
    assert len(quarantined) == 6
    assert all(row["_dq_reason"] for row in quarantined)

    with (tmp_path / "orders_clean.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == 24

    report = json.loads((tmp_path / "orders_report.json").read_text())
    assert report["summary"]["quarantined_rows"] == 6
    assert (tmp_path / "orders_report.md").exists()


def test_strict_mode_fails_on_quarantine(examples_dir, tmp_path):
    code, _, _ = _validate(examples_dir, tmp_path, "--strict", "--quiet")
    assert code == EXIT_BLOCKED


def test_blocked_batch_exit_code(tmp_path):
    (tmp_path / "d.csv").write_text("id\n1\n")
    (tmp_path / "c.json").write_text(json.dumps({"checks": [{"type": "row_count", "min": 5}]}))
    code, out, _ = _run(
        ["validate", "--data", str(tmp_path / "d.csv"), "--contract", str(tmp_path / "c.json"),
         "--out", str(tmp_path / "out")]
    )
    assert code == EXIT_BLOCKED
    assert "Blocked:" in out


def test_bad_contract_is_a_usage_error(tmp_path):
    (tmp_path / "d.csv").write_text("id\n1\n")
    (tmp_path / "c.json").write_text('{"checks": [{"type": "nope"}]}')
    code, _, err = _run(
        ["validate", "--data", str(tmp_path / "d.csv"), "--contract", str(tmp_path / "c.json"),
         "--out", str(tmp_path / "out")]
    )
    assert code == EXIT_ERROR
    assert "unknown check type" in err


def test_infer_schema_prints_contract_snippet(examples_dir):
    code, out, _ = _run(["infer-schema", "--data", str(examples_dir / "customers.csv")])
    assert code == EXIT_OK
    columns = json.loads(out)["schema"]["columns"]
    assert columns == {
        "customer_id": "integer",
        "full_name": "string",
        "country": "string",
        "signup_date": "date",
    }


def test_jsonl_example_runs_via_module_entry_point(examples_dir, tmp_path):
    proc = subprocess.run(
        [
            sys.executable, "-m", "data_quality_guard", "validate",
            "--data", str(examples_dir / "events.jsonl"),
            "--contract", str(examples_dir / "events_contract.json"),
            "--out", str(tmp_path), "--quiet",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_OK, proc.stderr
    assert proc.stdout.startswith("QUARANTINED web_events")
    assert (tmp_path / "events_quarantine.jsonl").read_text().count("\n") == 2
