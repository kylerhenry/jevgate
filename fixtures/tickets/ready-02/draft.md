# Add `ledger report trial-balance` to the CLI

## Why

Accountants closing a period fetch the trial balance from the HTTP API with curl and paste it into a spreadsheet. The CLI they already use for `ledger close` has no report command, so the close checklist mixes two tools and two logins.

## What

The Ledger CLI (`src/ledger/cli.py`) gains `ledger report trial-balance --period <YYYY-MM> [--tenant T] [--json]`. It calls the existing reporting service, which reads `v_trial_balance` through the Postgres store. No SQL is added outside `src/ledger/storage/postgres.py`.

Plain output is one line per account: account, debit and credit in minor units. `--json` prints the same object `GET /v1/reports/trial-balance` returns.

Out of scope: no new report types and no change to the HTTP API.

### Behaviour

`ledger report trial-balance --period 2026-08` prints the accounts of that period with their balances and exits 0. An unknown period exits 2 with `error: period 2026-13 not found` on stderr.

## Acceptance

- `ledger report trial-balance --period 2026-08 --json` prints the same JSON as `GET /v1/reports/trial-balance?period=2026-08` for the test tenant.
- `ledger report trial-balance --period 2026-13` exits 2 and prints `error: period 2026-13 not found` on stderr.
- `test_cli.py::test_report_trial_balance` passes and asserts that no query touches the `entries` table.

## Context

- Reports read from the reporting views (`v_trial_balance`), never from the entries table.
- The Ledger CLI is a thin wrapper over the same services the API router calls; `--json` mirrors the API shapes.
- The HTTP API serves `/v1/reports/<name>` through the API router.
