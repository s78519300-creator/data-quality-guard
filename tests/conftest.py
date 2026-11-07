"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from data_quality_guard.contract import Contract, parse_contract
from data_quality_guard.datasets import Dataset

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
FIXED_NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def make_contract(tmp_path: Path):
    """Build a contract from a plain dict, rooted in a temp directory."""

    def _make(checks: list[dict[str, Any]], **extra: Any) -> Contract:
        return parse_contract({"dataset": "test", "checks": checks, **extra}, base_dir=tmp_path)

    return _make


@pytest.fixture
def orders() -> Dataset:
    """A tiny, hand-written orders batch with one obvious problem per column."""
    rows = [
        {"id": "1", "email": "a@example.com", "amount": "10.5", "status": "new"},
        {"id": "2", "email": "b@example.com", "amount": "-3", "status": "new"},
        {"id": "2", "email": None, "amount": "7", "status": "shipped"},
        {"id": None, "email": "broken@", "amount": "abc", "status": "lost"},
    ]
    return Dataset(columns=["id", "email", "amount", "status"], rows=rows, source="test")
