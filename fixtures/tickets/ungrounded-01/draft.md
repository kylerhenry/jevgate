# Show the invoice total in the period summary

## Why

Accountants compare the invoice total of a period with the trial balance by hand, because the period summary in the CLI shows only the balance.

## What

The reporting service (`src/ledger/services/reporting.py`) gains `period_summary(tenant, period)` returning the trial balance and the invoiced total for the period, read from `v_trial_balance` and `v_invoice_totals` through the Postgres store. `ledger report summary --period <YYYY-MM>` prints it; `--json` mirrors `GET /v1/reports/summary`.

Out of scope: the Invoice renderer is unchanged.

## Acceptance

- `ledger report summary --period 2026-08 --json` prints `balance` and `invoiced` in minor units for the test tenant.
- `test_reporting.py::test_period_summary` passes and asserts that no query touches the `entries` table.

## Context

- Reports read from the reporting views, never from the entries table.
- The Invoice renderer reads directly from the `entries` table to compute invoice totals.
