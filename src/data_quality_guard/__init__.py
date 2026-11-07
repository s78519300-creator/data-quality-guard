"""data-quality-guard: declarative data-quality checks for ETL pipelines.

The package is intentionally dependency-free so it can run anywhere a Python
interpreter is available: inside an Airflow worker, a CI runner, or a small
Lambda that inspects files before they are loaded into a warehouse.

Typical programmatic use::

    from data_quality_guard import load_contract, load_dataset, run_validation

    contract = load_contract("orders_contract.json")
    dataset = load_dataset("orders.csv")
    result = run_validation(dataset, contract)
    print(result.status)
"""

from data_quality_guard.alerts import Alert, AlertSink, ConsoleSink, WebhookSink
from data_quality_guard.contract import Contract, ContractError, load_contract
from data_quality_guard.datasets import Dataset, load_dataset
from data_quality_guard.engine import RunResult, RunStatus, run_validation
from data_quality_guard.schema import DriftReport, detect_drift, infer_schema

__version__ = "0.1.0"

__all__ = [
    "Alert",
    "AlertSink",
    "ConsoleSink",
    "Contract",
    "ContractError",
    "Dataset",
    "DriftReport",
    "RunResult",
    "RunStatus",
    "WebhookSink",
    "__version__",
    "detect_drift",
    "infer_schema",
    "load_contract",
    "load_dataset",
    "run_validation",
]
