"""Run reports in JSON (for machines) and Markdown (for humans and PR comments)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from data_quality_guard.engine import RunResult


def build_report(result: RunResult, tool_version: str = "") -> dict[str, Any]:
    """Return the full run report as a JSON-serialisable dictionary."""
    return {
        "tool": "data-quality-guard",
        "version": tool_version,
        "dataset": result.dataset,
        "source": result.source,
        "started_at": result.started_at,
        "duration_ms": round(result.duration_ms, 2),
        "summary": result.summary(),
        "blocked_reasons": list(result.blocked_reasons),
        "schema_drift": {
            **(result.drift.to_dict() if result.drift else {"has_drift": False}),
            "findings": [
                {
                    "kind": f.kind,
                    "column": f.column,
                    "detail": f.detail,
                    "severity": f.severity.value,
                }
                for f in result.drift_findings
            ],
        },
        "checks": [c.to_dict() for c in result.checks],
    }


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(result: RunResult, tool_version: str = "") -> str:
    """Render a compact Markdown report suitable for a PR comment or wiki page."""
    s = result.summary()
    lines = [
        f"# Data quality report: `{result.dataset}`",
        "",
        f"**Status:** {result.status.value}  ",
        f"**Source:** `{result.source}`  ",
        f"**Run at:** {result.started_at}"
        + (f" (data-quality-guard {tool_version})" if tool_version else ""),
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Rows received | {s['total_rows']} |",
        f"| Clean rows | {s['clean_rows']} |",
        f"| Quarantined rows | {s['quarantined_rows']} |",
        f"| Critical failure rate | {result.critical_rate:.1%} "
        f"(limit {result.max_critical_rate:.1%}) |",
        f"| Checks passed | {s['checks_passed']} / {s['checks_total']} |",
        f"| Schema drift events | {s['drift_events']} |",
        "",
    ]

    if result.blocked_reasons:
        lines += ["## Why the batch was blocked", ""]
        lines += [f"- {reason}" for reason in result.blocked_reasons]
        lines.append("")

    lines += ["## Schema drift", ""]
    if result.drift is None:
        lines += ["_No expected schema in the contract._", ""]
    elif not result.drift_findings:
        lines += ["No drift detected.", ""]
    else:
        lines += ["| Change | Column | Detail | Severity |", "|---|---|---|---|"]
        lines += [
            f"| {f.kind} | `{f.column}` | {_cell(f.detail)} | {f.severity.value} |"
            for f in result.drift_findings
        ]
        lines.append("")

    lines += [
        "## Checks",
        "",
        "| Result | Check | Type | Column | Severity | Failed | Details |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in result.checks:
        mark = "PASS" if c.passed else "FAIL"
        failed = c.failed_count if c.row_level else ("-" if c.passed else "batch")
        lines.append(
            f"| {mark} | {c.name} | {c.check_type} | {_cell(c.column or '-')} | "
            f"{c.severity.value} | {failed} | {_cell(c.message)} |"
        )
    lines.append("")

    failing = [c for c in result.checks if not c.passed and c.samples]
    if failing:
        lines += ["## Sample failing values", ""]
        for c in failing:
            sample = ", ".join("`null`" if v is None else f"`{_cell(v)}`" for v in c.samples)
            lines.append(f"- **{c.name}**: {sample}")
        lines.append("")
    return "\n".join(lines)


def write_reports(
    result: RunResult, out_dir: Union[str, Path], stem: str, tool_version: str = ""
) -> tuple[Path, Path]:
    """Write ``<stem>_report.json`` and ``<stem>_report.md`` into ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}_report.json"
    md_path = out / f"{stem}_report.md"
    json_path.write_text(
        json.dumps(build_report(result, tool_version), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(result, tool_version), encoding="utf-8")
    return json_path, md_path
