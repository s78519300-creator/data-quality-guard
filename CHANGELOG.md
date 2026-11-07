# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Nothing yet.

## [0.1.0] - 2026-11-08

### Added

- JSON data contracts with nine check types: `not_null`, `unique`, `in_range`,
  `allowed_values`, `regex`, `type`, `referential`, `freshness`, `row_count`.
- Schema drift detection (added, removed and type-changed columns) with
  per-kind severity.
- Severity model (`info` / `warn` / `critical`), row quarantine with a
  `_dq_reason` column, and a configurable `max_critical_rate` that blocks a batch.
- CSV, TSV and JSON Lines input; clean and quarantine outputs keep the input format.
- JSON and Markdown run reports.
- Alert sinks: `ConsoleSink` and `WebhookSink` (generic JSON or Slack-style text).
- `dqguard validate` and `dqguard infer-schema` commands, plus `python -m data_quality_guard`.
- CI on Python 3.9 to 3.12 with ruff and an end-to-end run on the example data.
