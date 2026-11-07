"""Data contracts: the JSON file that declares what "good data" means.

A contract has four optional sections::

    {
      "dataset": "orders",
      "schema": {"columns": {"order_id": "integer"}, "drift_severity": {...}},
      "quarantine": {"max_critical_rate": 0.25},
      "checks": [{"name": "...", "type": "not_null", "column": "order_id"}]
    }

Contracts are validated up front so that a typo fails fast with a clear
message instead of silently skipping a check in production.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from data_quality_guard.schema import KNOWN_TYPES


class ContractError(ValueError):
    """Raised for malformed or inconsistent contract files."""


class Severity(str, Enum):
    """How much a failed check matters.

    * ``info``     - recorded in the report only.
    * ``warn``     - reported and alerted, data still flows.
    * ``critical`` - failing rows are quarantined; dataset-level failures block the batch.
    """

    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warn": 1, "critical": 2}[self.value]

    @classmethod
    def parse(cls, value: Any, where: str = "") -> Severity:
        try:
            return cls(str(value).lower())
        except ValueError:
            allowed = ", ".join(s.value for s in cls)
            raise ContractError(f"{where}invalid severity '{value}' (use {allowed})") from None


# check type -> (requires a column?, required params)
CHECK_TYPES: dict[str, tuple[bool, tuple[str, ...]]] = {
    "not_null": (True, ()),
    "unique": (True, ()),
    "in_range": (True, ()),
    "allowed_values": (True, ("values",)),
    "regex": (True, ("pattern",)),
    "type": (True, ("expected",)),
    "referential": (True, ("dataset", "ref_column")),
    "freshness": (True, ("max_age_hours",)),
    "row_count": (False, ()),
}

DEFAULT_DRIFT_SEVERITY = {
    "added": Severity.WARN,
    "removed": Severity.CRITICAL,
    "type_changed": Severity.CRITICAL,
}


@dataclass
class CheckSpec:
    """One declared check from the contract."""

    name: str
    type: str
    column: Optional[str] = None
    severity: Severity = Severity.CRITICAL
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class SchemaSpec:
    """Expected columns and how seriously to treat each kind of drift."""

    columns: dict[str, str] = field(default_factory=dict)
    drift_severity: dict[str, Severity] = field(
        default_factory=lambda: dict(DEFAULT_DRIFT_SEVERITY)
    )


@dataclass
class Contract:
    """A parsed and validated data contract."""

    dataset: str
    checks: list[CheckSpec] = field(default_factory=list)
    schema: Optional[SchemaSpec] = None
    max_critical_rate: float = 0.1
    description: str = ""
    base_dir: Path = field(default_factory=Path.cwd)

    def resolve(self, relative: Union[str, Path]) -> Path:
        """Resolve a path mentioned in the contract relative to the contract file."""
        path = Path(relative)
        return path if path.is_absolute() else self.base_dir / path


def load_contract(path: Union[str, Path]) -> Contract:
    """Read and validate a contract from a JSON file."""
    path = Path(path)
    if not path.is_file():
        raise ContractError(f"Contract file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    return parse_contract(raw, base_dir=path.resolve().parent)


def parse_contract(raw: Any, base_dir: Optional[Path] = None) -> Contract:
    """Validate a contract that has already been decoded from JSON."""
    if not isinstance(raw, dict):
        raise ContractError("Contract must be a JSON object")

    checks_raw = raw.get("checks", [])
    if not isinstance(checks_raw, list):
        raise ContractError("'checks' must be a list")

    checks: list[CheckSpec] = []
    names: set[str] = set()
    for index, item in enumerate(checks_raw):
        spec = _parse_check(item, index)
        if spec.name in names:
            raise ContractError(f"Duplicate check name '{spec.name}'")
        names.add(spec.name)
        checks.append(spec)

    quarantine = raw.get("quarantine", {}) or {}
    rate = quarantine.get("max_critical_rate", 0.1)
    if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
        raise ContractError("quarantine.max_critical_rate must be a number between 0 and 1")

    return Contract(
        dataset=str(raw.get("dataset", "dataset")),
        checks=checks,
        schema=_parse_schema(raw["schema"]) if raw.get("schema") else None,
        max_critical_rate=float(rate),
        description=str(raw.get("description", "")),
        base_dir=base_dir or Path.cwd(),
    )


def _parse_check(item: Any, index: int) -> CheckSpec:
    where = f"checks[{index}]: "
    if not isinstance(item, dict):
        raise ContractError(f"{where}each check must be an object")
    check_type = item.get("type")
    if check_type not in CHECK_TYPES:
        allowed = ", ".join(sorted(CHECK_TYPES))
        raise ContractError(f"{where}unknown check type '{check_type}' (known: {allowed})")

    needs_column, required = CHECK_TYPES[check_type]
    column = item.get("column")
    if needs_column and not column:
        raise ContractError(f"{where}'{check_type}' requires a 'column'")

    reserved = {"name", "type", "column", "severity", "description"}
    params = {k: v for k, v in item.items() if k not in reserved}
    missing = [p for p in required if p not in params]
    if missing:
        raise ContractError(f"{where}'{check_type}' is missing: {', '.join(missing)}")

    if check_type == "type" and params["expected"] not in KNOWN_TYPES:
        raise ContractError(f"{where}unknown expected type '{params['expected']}'")
    if check_type == "in_range" and "min" not in params and "max" not in params:
        raise ContractError(f"{where}'in_range' needs at least one of 'min' or 'max'")
    if check_type == "row_count" and "min" not in params and "max" not in params:
        raise ContractError(f"{where}'row_count' needs at least one of 'min' or 'max'")
    if check_type == "allowed_values" and not isinstance(params["values"], list):
        raise ContractError(f"{where}'values' must be a list")

    default_name = f"{column}_{check_type}" if column else check_type
    return CheckSpec(
        name=str(item.get("name") or default_name),
        type=check_type,
        column=column,
        severity=Severity.parse(item.get("severity", "critical"), where),
        params=params,
        description=str(item.get("description", "")),
    )


def _parse_schema(raw: Any) -> SchemaSpec:
    if not isinstance(raw, dict) or not isinstance(raw.get("columns"), dict):
        raise ContractError("'schema' must contain a 'columns' object")
    columns: dict[str, str] = {}
    for name, col_type in raw["columns"].items():
        if col_type not in KNOWN_TYPES:
            raise ContractError(f"schema.columns.{name}: unknown type '{col_type}'")
        columns[name] = col_type

    severity = dict(DEFAULT_DRIFT_SEVERITY)
    for kind, value in (raw.get("drift_severity") or {}).items():
        if kind not in severity:
            raise ContractError(f"schema.drift_severity: unknown drift kind '{kind}'")
        severity[kind] = Severity.parse(value, f"schema.drift_severity.{kind}: ")
    return SchemaSpec(columns=columns, drift_severity=severity)
