# Balance report ignores entries posted after the period end

## Why

`balance(period)` sums every row of the balance view for the period, including entries whose `posted_at` falls after the period's end date (back-dated corrections land there). Month-end balances therefore differ from the closed books.

## What

In `src/ledger/services/reports.py`, `balance` keeps only view rows whose `posted_at` is on or before `period.end` before summing. The reporting view itself is unchanged.

## Acceptance

- `balance(period)` excludes entries whose `posted_at` is after `period.end`.
- Entries posted on `period.end` itself are included in the balance.
- Tests cover an entry after the end date and an entry on the end date.

## Context

- `Period.end` is a date; `posted_at` on the view rows is a date as well.
- Reports read from `v_balance`, never from the entries table.
