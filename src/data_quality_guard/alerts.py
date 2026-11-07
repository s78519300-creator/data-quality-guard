"""Alerting hooks.

An :class:`AlertSink` receives one :class:`Alert` per run that did not pass
cleanly. Two sinks ship with the package:

* :class:`ConsoleSink` writes a short, grep-friendly block to stderr.
* :class:`WebhookSink` POSTs a JSON payload (generic or Slack-style) to a URL.

Writing your own sink (PagerDuty, SNS, a database table) is a matter of
subclassing ``AlertSink`` and implementing ``send``.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TextIO

from data_quality_guard.contract import Severity
from data_quality_guard.engine import RunResult, RunStatus


@dataclass
class Alert:
    """What went wrong in a run, condensed for humans and machines."""

    dataset: str
    status: str
    severity: str
    title: str
    failures: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result: RunResult) -> Alert:
        failures = [
            {
                "check": c.name,
                "severity": c.severity.value,
                "column": c.column,
                "message": c.message,
            }
            for c in result.failed_checks
        ]
        failures += [
            {
                "check": f"schema_drift.{f.kind}",
                "severity": f.severity.value,
                "column": f.column,
                "message": f.detail,
            }
            for f in result.drift_findings
        ]
        highest = result.highest_failed_severity()
        return cls(
            dataset=result.dataset,
            status=result.status.value,
            severity=highest.value if highest else Severity.INFO.value,
            title=f"[{result.status.value}] data quality run for '{result.dataset}'",
            failures=failures,
            summary=result.summary(),
        )


class AlertSink(ABC):
    """Base class for alert destinations.

    ``min_severity`` filters noise: a sink configured with ``critical`` will
    ignore runs whose worst failure was only a warning.
    """

    def __init__(self, min_severity: Severity = Severity.WARN) -> None:
        self.min_severity = min_severity

    def should_send(self, alert: Alert) -> bool:
        return Severity(alert.severity).rank >= self.min_severity.rank

    def notify(self, alert: Alert) -> bool:
        """Send ``alert`` if it passes the severity filter. Returns True if sent."""
        if not self.should_send(alert):
            return False
        self.send(alert)
        return True

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver the alert. Implementations should raise on hard failures."""


class ConsoleSink(AlertSink):
    """Print alerts to a stream (stderr by default)."""

    def __init__(
        self, stream: Optional[TextIO] = None, min_severity: Severity = Severity.WARN
    ) -> None:
        super().__init__(min_severity)
        self.stream = stream

    def send(self, alert: Alert) -> None:
        out = self.stream or sys.stderr
        print(f"ALERT {alert.severity.upper():<8} {alert.title}", file=out)
        for failure in alert.failures:
            if Severity(failure["severity"]).rank < self.min_severity.rank:
                continue
            column = f" ({failure['column']})" if failure.get("column") else ""
            print(
                f"  - [{failure['severity']}] {failure['check']}{column}: {failure['message']}",
                file=out,
            )


Transport = Callable[[str, bytes, dict[str, str]], Any]


def _urllib_transport(url: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return response.status


class WebhookSink(AlertSink):
    """POST alerts as JSON to an HTTP endpoint.

    ``style="slack"`` produces a Slack/Teams-compatible ``{"text": ...}``
    payload; the default ``"generic"`` style sends the full alert structure.
    The ``transport`` argument exists so tests (and unusual environments) can
    replace the HTTP call.
    """

    def __init__(
        self,
        url: str,
        style: str = "generic",
        min_severity: Severity = Severity.WARN,
        transport: Optional[Transport] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(min_severity)
        if style not in ("generic", "slack"):
            raise ValueError("style must be 'generic' or 'slack'")
        self.url = url
        self.style = style
        self.transport = transport or _urllib_transport
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def build_payload(self, alert: Alert) -> dict[str, Any]:
        if self.style == "slack":
            lines = [f"*{alert.title}*"]
            s = alert.summary
            if s:
                lines.append(
                    f"rows={s.get('total_rows')} clean={s.get('clean_rows')} "
                    f"quarantined={s.get('quarantined_rows')}"
                )
            lines += [
                f"- `{f['check']}` [{f['severity']}] {f['message']}" for f in alert.failures
            ]
            return {"text": "\n".join(lines)}
        return {
            "source": "data-quality-guard",
            "dataset": alert.dataset,
            "status": alert.status,
            "severity": alert.severity,
            "title": alert.title,
            "summary": alert.summary,
            "failures": alert.failures,
        }

    def send(self, alert: Alert) -> None:
        body = json.dumps(self.build_payload(alert)).encode("utf-8")
        self.transport(self.url, body, dict(self.headers))


def dispatch(result: RunResult, sinks: list[AlertSink]) -> Optional[Alert]:
    """Build an alert for ``result`` and hand it to every sink.

    Clean runs produce no alert. Returns the alert that was built, if any.
    """
    if result.status is RunStatus.PASSED:
        return None
    alert = Alert.from_result(result)
    for sink in sinks:
        sink.notify(alert)
    return alert
