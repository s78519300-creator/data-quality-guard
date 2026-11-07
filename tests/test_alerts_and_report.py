from __future__ import annotations

import io
import json

import pytest

from data_quality_guard.alerts import Alert, ConsoleSink, WebhookSink, dispatch
from data_quality_guard.contract import Severity
from data_quality_guard.datasets import Dataset
from data_quality_guard.engine import run_validation
from data_quality_guard.report import build_report, render_markdown, write_reports


@pytest.fixture
def warned_result(orders, make_contract):
    contract = make_contract(
        [
            {"type": "not_null", "column": "email", "severity": "warn"},
            {"type": "row_count", "min": 1},
        ],
        schema={"columns": {"id": "integer", "email": "string", "amount": "string"}},
    )
    return run_validation(orders, contract)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, body: bytes, headers: dict) -> int:
        self.calls.append((url, json.loads(body), headers))
        return 200


def test_alert_collects_check_and_drift_failures(warned_result):
    alert = Alert.from_result(warned_result)
    checks = [f["check"] for f in alert.failures]
    assert "email_not_null" in checks
    assert "schema_drift.added" in checks  # the unexpected 'status' column
    assert alert.severity == "warn"


def test_webhook_sink_generic_payload(warned_result):
    transport = RecordingTransport()
    sink = WebhookSink("https://hooks.example.test/dq", transport=transport)
    assert sink.notify(Alert.from_result(warned_result))
    url, payload, headers = transport.calls[0]
    assert url == "https://hooks.example.test/dq"
    assert payload["source"] == "data-quality-guard"
    assert payload["status"] == "WARNED"
    assert headers["Content-Type"] == "application/json"


def test_webhook_sink_slack_payload_is_text(warned_result):
    sink = WebhookSink("https://x.test", style="slack", transport=RecordingTransport())
    payload = sink.build_payload(Alert.from_result(warned_result))
    assert set(payload) == {"text"}
    assert "email_not_null" in payload["text"]


def test_min_severity_filters_alerts(warned_result):
    transport = RecordingTransport()
    sink = WebhookSink("https://x.test", min_severity=Severity.CRITICAL, transport=transport)
    assert sink.notify(Alert.from_result(warned_result)) is False
    assert transport.calls == []


def test_console_sink_and_dispatch(warned_result):
    stream = io.StringIO()
    alert = dispatch(warned_result, [ConsoleSink(stream=stream)])
    assert alert is not None
    assert "ALERT WARN" in stream.getvalue()
    assert "email_not_null" in stream.getvalue()


def test_dispatch_skips_clean_runs(make_contract):
    data = Dataset(columns=["id"], rows=[{"id": "1"}])
    result = run_validation(data, make_contract([{"type": "not_null", "column": "id"}]))
    stream = io.StringIO()
    assert dispatch(result, [ConsoleSink(stream=stream)]) is None
    assert stream.getvalue() == ""


def test_reports_contain_checks_and_drift(warned_result, tmp_path):
    report = build_report(warned_result, "0.1.0")
    assert report["summary"]["status"] == "WARNED"
    assert report["schema_drift"]["added"] == {"status": "string"}
    assert {c["name"] for c in report["checks"]} == {"email_not_null", "row_count"}

    markdown = render_markdown(warned_result)
    assert "| FAIL | email_not_null |" in markdown
    assert "## Schema drift" in markdown

    json_path, md_path = write_reports(warned_result, tmp_path / "out", "orders")
    assert json.loads(json_path.read_text())["dataset"] == "test"
    assert md_path.read_text().startswith("# Data quality report")
