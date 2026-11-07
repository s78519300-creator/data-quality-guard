"""Reading and writing tabular files (CSV and JSON Lines).

Everything is kept in memory as a list of dictionaries. That is a deliberate
trade-off: the tool targets batch files of the size you would sanity-check
before a load (thousands to a few million rows), not full table scans.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

Row = dict[str, Any]
PathLike = Union[str, Path]

CSV_SUFFIXES = {".csv", ".tsv"}
JSONL_SUFFIXES = {".jsonl", ".ndjson"}


class DatasetError(ValueError):
    """Raised when an input file cannot be read as a dataset."""


@dataclass
class Dataset:
    """An in-memory batch of rows plus the column order seen in the source."""

    columns: list[str]
    rows: list[Row]
    source: str = "<memory>"
    fmt: str = "csv"
    name: Optional[str] = None

    def __len__(self) -> int:
        return len(self.rows)

    def values(self, column: str) -> list[Any]:
        """Return every value of ``column`` (``None`` where it is absent)."""
        return [row.get(column) for row in self.rows]

    @classmethod
    def from_rows(cls, rows: list[Row], name: Optional[str] = None) -> Dataset:
        """Build a dataset from dictionaries, preserving first-seen column order."""
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        return cls(columns=columns, rows=rows, name=name, fmt="jsonl")


def detect_format(path: PathLike) -> str:
    """Return ``"csv"`` or ``"jsonl"`` based on the file extension."""
    suffix = Path(path).suffix.lower()
    if suffix in CSV_SUFFIXES:
        return "csv"
    if suffix in JSONL_SUFFIXES:
        return "jsonl"
    raise DatasetError(
        f"Unsupported file type '{suffix or path}'. Use .csv, .tsv, .jsonl or .ndjson."
    )


def load_dataset(path: PathLike, fmt: Optional[str] = None) -> Dataset:
    """Load a CSV or JSONL file.

    In CSV files an empty cell is treated as a null so that ``not_null`` checks
    behave the same for both formats.
    """
    path = Path(path)
    if not path.is_file():
        raise DatasetError(f"Data file not found: {path}")
    fmt = fmt or detect_format(path)
    if fmt == "csv":
        dataset = _load_csv(path)
    elif fmt == "jsonl":
        dataset = _load_jsonl(path)
    else:
        raise DatasetError(f"Unknown format: {fmt}")
    dataset.name = path.stem
    return dataset


def _load_csv(path: Path) -> Dataset:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise DatasetError(f"{path} is empty or has no header row")
        columns = [name.strip() for name in reader.fieldnames]
        rows: list[Row] = []
        for raw in reader:
            row: Row = {}
            for original, column in zip(reader.fieldnames, columns):
                value = raw.get(original)
                if isinstance(value, str):
                    value = value.strip()
                row[column] = value if value not in ("", None) else None
            rows.append(row)
    return Dataset(columns=columns, rows=rows, source=str(path), fmt="csv")


def _load_jsonl(path: Path) -> Dataset:
    rows: list[Row] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path}:{line_no}: invalid JSON ({exc.msg})") from exc
            if not isinstance(record, dict):
                raise DatasetError(f"{path}:{line_no}: expected a JSON object per line")
            rows.append(record)
    dataset = Dataset.from_rows(rows)
    dataset.source = str(path)
    return dataset


def write_rows(
    path: PathLike, columns: list[str], rows: Iterable[Row], fmt: str = "csv"
) -> Path:
    """Write rows to ``path`` as CSV or JSONL, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                record = {col: row.get(col) for col in columns}
                handle.write(json.dumps(record, default=str) + "\n")
        return path

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: "" if row.get(col) is None else row.get(col) for col in columns})
    return path
