"""Validation engine: drift -> checks -> quarantine -> verdict.

The engine is pure: it takes a dataset and a contract and returns a
:class:`RunResult`. Writing files and sending alerts happen in the CLI (or in
your own orchestration code), which keeps the core easy to test and embed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from data_quality_guard.checks import CheckContext, CheckResult, run_check
from data_quality_guard.contract import Contract, Severity
from data_quality_guard.datasets import Dataset, Row
from data_quality_guard.schema import DriftReport, detect_drift

REASON_COLUMN = "_dq_reason"


class RunStatus(str, Enum):
    """Overall verdict for a batch."""

    PASSED = "PASSED"  # nothing above info failed
    WARNED = "WARNED"  # warnings only, every row is clean
    QUARANTINED = "QUARANTINED"  # some rows quarantined, the rest may load
    BLOCKED = "BLOCKED"  # do not load this batch

    @property
    def ok(self) -> bool:
        return self is not RunStatus.BLOCKED


@dataclass
class DriftFinding:
    """A single drift event with the severity assigned by the contract."""

    kind: str  # added | removed | type_changed
    column: str
    detail: str
    severity: Severity


@dataclass
class RunResult:
    """Everything a run produced, ready to be reported or written out."""

    dataset: str
    source: str
    columns: list[str]
    total_rows: int
    checks: list[CheckResult]
    drift: Optional[DriftReport]
    drift_findings: list[DriftFinding]
    clean_rows: list[Row]
    quarantined_rows: list[Row]
    critical_rate: float
    max_critical_rate: float
    status: RunStatus
    blocked_reasons: list[str] = field(default_factory=list)
    started_at: str = ""
    duration_ms: float = 0.0

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def highest_failed_severity(self) -> Optional[Severity]:
        severities = [c.severity for c in self.failed_checks]
        severities += [f.severity for f in self.drift_findings]
        return max(severities, key=lambda s: s.rank) if severities else None

    def summary(self) -> dict[str, Any]:
        failed = self.failed_checks
        return {
            "status": self.status.value,
            "total_rows": self.total_rows,
            "clean_rows": len(self.clean_rows),
            "quarantined_rows": len(self.quarantined_rows),
            "critical_rate": round(self.critical_rate, 4),
            "max_critical_rate": self.max_critical_rate,
            "checks_total": len(self.checks),
            "checks_passed": len(self.checks) - len(failed),
            "checks_failed": len(failed),
            "failed_by_severity": {
                s.value: sum(1 for c in failed if c.severity is s) for s in Severity
            },
            "drift_events": len(self.drift_findings),
        }


def _drift_findings(drift: DriftReport, contract: Contract) -> list[DriftFinding]:
    assert contract.schema is not None
    levels = contract.schema.drift_severity
    findings = [
        DriftFinding("added", col, f"new column ({typ})", levels["added"])
        for col, typ in drift.added.items()
    ]
    findings += [
        DriftFinding("removed", col, f"expected {typ}, column missing", levels["removed"])
        for col, typ in drift.removed.items()
    ]
    findings += [
        DriftFinding(
            "type_changed", c.column, f"{c.expected} -> {c.observed}", levels["type_changed"]
        )
        for c in drift.type_changed
    ]
    return findings


def run_validation(
    dataset: Dataset, contract: Contract, now: Optional[datetime] = None
) -> RunResult:
    """Validate ``dataset`` against ``contract`` and decide what happens to it."""
    started = datetime.now(timezone.utc)
    ctx = CheckContext(contract=contract, now=now or started)

    drift: Optional[DriftReport] = None
    findings: list[DriftFinding] = []
    if contract.schema is not None:
        drift = detect_drift(contract.schema.columns, dataset)
        findings = _drift_findings(drift, contract)

    results = [run_check(dataset, spec, ctx) for spec in contract.checks]

    # Collect quarantine reasons per row from critical row-level failures.
    reasons: dict[int, list[str]] = {}
    for result in results:
        if result.passed or result.severity is not Severity.CRITICAL or not result.row_level:
            continue
        for index in result.failed_rows:
            reasons.setdefault(index, []).append(result.name)

    clean: list[Row] = []
    quarantined: list[Row] = []
    for index, row in enumerate(dataset.rows):
        if index in reasons:
            quarantined.append({**row, REASON_COLUMN: ";".join(reasons[index])})
        else:
            clean.append(row)

    total = len(dataset)
    critical_rate = len(quarantined) / total if total else 0.0

    blocked: list[str] = []
    if critical_rate > contract.max_critical_rate:
        blocked.append(
            f"critical failure rate {critical_rate:.1%} exceeds "
            f"limit {contract.max_critical_rate:.1%}"
        )
    for result in results:
        if not result.passed and not result.row_level and result.severity is Severity.CRITICAL:
            blocked.append(f"critical check '{result.name}' failed: {result.message}")
    for finding in findings:
        if finding.severity is Severity.CRITICAL:
            blocked.append(f"critical schema drift on '{finding.column}': {finding.detail}")

    warned = any(
        (not r.passed and r.severity is Severity.WARN) for r in results
    ) or any(f.severity is Severity.WARN for f in findings)

    if blocked:
        status = RunStatus.BLOCKED
    elif quarantined:
        status = RunStatus.QUARANTINED
    elif warned:
        status = RunStatus.WARNED
    else:
        status = RunStatus.PASSED

    finished = datetime.now(timezone.utc)
    return RunResult(
        dataset=contract.dataset,
        source=dataset.source,
        columns=list(dataset.columns),
        total_rows=total,
        checks=results,
        drift=drift,
        drift_findings=findings,
        clean_rows=clean,
        quarantined_rows=quarantined,
        critical_rate=critical_rate,
        max_critical_rate=contract.max_critical_rate,
        status=status,
        blocked_reasons=blocked,
        started_at=started.isoformat(timespec="seconds"),
        duration_ms=(finished - started).total_seconds() * 1000,
    )
