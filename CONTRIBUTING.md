# Contributing

Thanks for taking the time to improve data-quality-guard. Bug reports, new
check types and documentation fixes are all welcome.

## Development setup

```bash
git clone https://github.com/s78519300-creator/data-quality-guard.git
cd data-quality-guard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
pytest
dqguard validate --data examples/orders.csv --contract examples/orders_contract.json --out out/
```

## Guidelines

- The runtime must stay dependency-free (standard library only). Dev tools are fine.
- Every new check type needs an entry in `CHECK_TYPES` (contract validation),
  an implementation in `checks.py`, tests, and a line in the README check table.
- Keep the tests offline. Alert sinks accept an injectable transport for exactly this reason.
- Use clear commit messages and add a line to `CHANGELOG.md` under "Unreleased".
