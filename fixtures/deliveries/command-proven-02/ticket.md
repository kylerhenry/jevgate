# Reject postings into a closed period

## Why

Entries can be posted into a period that has already been closed, which silently changes books that accountants treat as final. Two tenants have reported closed-period balances drifting after month end.

## What

In `src/ledger/services/posting.py`, `post_entry` looks up the entry's period through the store and raises `PeriodClosedError` (a `LedgerError` subclass with code `period_closed`) before anything is written when that period is closed. In `src/ledger/api/router.py`, `error_response` maps `PeriodClosedError` to HTTP 409. No schema change. `ledger.report close-status --json` reports, per period, whether it is closed and how many entries were posted and rejected.

## Acceptance

- `post_entry` raises `PeriodClosedError` when the entry's period is closed and writes nothing.
- Posting to a closed period over the API returns 409 with error code `period_closed`.
- Posting to an open period behaves exactly as before.
- `python -m ledger.report close-status --period 2024-01 --json` prints JSON in which `periods[0].closed` is `true`, `periods[0].entries` is 12 and `periods[0].rejected` is 3.

## Context

- Periods close by inserting a `period_close` row; `Period.closed` is already exposed by the store.
- The CLI shares the posting service, so it needs no change.
