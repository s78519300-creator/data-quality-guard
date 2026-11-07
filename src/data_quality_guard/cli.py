"""Command-line interface: ``dqguard validate`` and ``dqguard infer-schema``.

Exit codes are designed for orchestrators:

* ``0`` - batch may be loaded (PASSED, WARNED or QUARANTINED)
* ``1`` - usage or configuration error (bad contract, unreadable file)
* ``2`` - batch BLOCKED (or WARNED/QUARANTINED with ``--strict``)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

from data_quality_guard import __version__
from data_quality_guard.alerts import AlertSink, ConsoleSink, WebhookSink, dispatch
from data_quality_guard.contract import ContractError, Severity, load_contract
from data_quality_guard.datasets import DatasetError, load_dataset, write_rows
from data_quality_guard.engine import REASON_COLUMN, RunResult, RunStatus, run_validation
from data_quality_guard.report import write_reports
from data_quality_guard.schema import infer_schema, parse_timestamp

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2

_DRIFT_SYMBOL = {"added": "+", "removed": "-", "type_changed": "~"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dqguard",
        description="Validate ETL batches against a declarative data contract.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a data file against a contract")
    validate.add_argument("--data", required=True, type=Path, help="CSV or JSONL file")
    validate.add_argument("--contract", required=True, type=Path, help="JSON contract file")
    validate.add_argument(
        "--out", type=Path, default=Path("out"), help="output directory (default: out/)"
    )
    validate.add_argument(
        "--now",
        help="reference time for freshness checks (ISO-8601); defaults to the current time",
    )
    validate.add_argument(
        "--webhook", help="POST an alert to this URL when the run is not clean"
    )
    validate.add_argument(
        "--webhook-style", choices=("generic", "slack"), default="generic"
    )
    validate.add_argument(
        "--alert-level",
        choices=[s.value for s in Severity],
        default=Severity.WARN.value,
        help="minimum severity that triggers an alert (default: warn)",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 on any warning or quarantine, not only on a blocked batch",
    )
    validate.add_argument("--quiet", action="store_true", help="only print the verdict line")

    infer = sub.add_parser(
        "infer-schema", help="print the inferred schema of a file as a contract snippet"
    )
    infer.add_argument("--data", required=True, type=Path)
    return parser


def _print_summary(result: RunResult, written: list[Path], out: TextIO) -> None:
    print(
        f"dqguard {__version__}  dataset={result.dataset}  "
        f"source={result.source}  rows={result.total_rows}",
        file=out,
    )
    print("", file=out)

    print("Schema drift", file=out)
    if result.drift is None:
        print("  (no expected schema in contract)", file=out)
    elif not result.drift_findings:
        print("  none", file=out)
    for finding in result.drift_findings:
        symbol = _DRIFT_SYMBOL[finding.kind]
        print(
            f"  {symbol} {finding.kind:<13}{finding.column:<18}"
            f"{finding.detail:<30}[{finding.severity.value}]",
            file=out,
        )
    print("", file=out)

    print("Checks", file=out)
    width = max((len(c.name) for c in result.checks), default=10) + 2
    for check in result.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(
            f"  {mark}  {check.name:<{width}}{check.severity.value:<10}{check.message}",
            file=out,
        )
    print("", file=out)

    for reason in result.blocked_reasons:
        print(f"Blocked: {reason}", file=out)
    print(
        f"Result: {result.status.value}  clean={len(result.clean_rows)}  "
        f"quarantined={len(result.quarantined_rows)}  "
        f"critical_rate={result.critical_rate:.1%} (limit {result.max_critical_rate:.1%})",
        file=out,
    )
    for path in written:
        print(f"  wrote {path}", file=out)


def cmd_validate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    now: Optional[datetime] = None
    if args.now:
        now = parse_timestamp(args.now)
        if now is None:
            print(f"error: --now is not a valid ISO-8601 timestamp: {args.now}", file=err)
            return EXIT_ERROR

    try:
        contract = load_contract(args.contract)
        dataset = load_dataset(args.data)
        result = run_validation(dataset, contract, now=now)
    except (ContractError, DatasetError, ValueError) as exc:
        print(f"error: {exc}", file=err)
        return EXIT_ERROR

    stem = dataset.name or "dataset"
    suffix = ".jsonl" if dataset.fmt == "jsonl" else ".csv"
    written = [
        write_rows(args.out / f"{stem}_clean{suffix}", dataset.columns, result.clean_rows,
                   dataset.fmt),
        write_rows(
            args.out / f"{stem}_quarantine{suffix}",
            [*dataset.columns, REASON_COLUMN],
            result.quarantined_rows,
            dataset.fmt,
        ),
        *write_reports(result, args.out, stem, __version__),
    ]

    if args.quiet:
        print(f"{result.status.value} {result.dataset} clean={len(result.clean_rows)} "
              f"quarantined={len(result.quarantined_rows)}", file=out)
    else:
        _print_summary(result, written, out)

    level = Severity(args.alert_level)
    sinks: list[AlertSink] = [ConsoleSink(stream=err, min_severity=level)]
    if args.webhook:
        sinks.append(WebhookSink(args.webhook, style=args.webhook_style, min_severity=level))
    try:
        dispatch(result, sinks)
    except OSError as exc:  # a broken webhook should not hide the verdict
        print(f"warning: alert delivery failed: {exc}", file=err)

    if result.status is RunStatus.BLOCKED:
        return EXIT_BLOCKED
    if args.strict and result.status is not RunStatus.PASSED:
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_infer_schema(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    try:
        dataset = load_dataset(args.data)
    except DatasetError as exc:
        print(f"error: {exc}", file=err)
        return EXIT_ERROR
    snippet = {"schema": {"columns": infer_schema(dataset)}}
    print(json.dumps(snippet, indent=2), file=out)
    return EXIT_OK


def main(
    argv: Optional[list[str]] = None,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    """Entry point for the ``dqguard`` console script."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args, out, err)
    return cmd_infer_schema(args, out, err)


if __name__ == "__main__":
    raise SystemExit(main())
