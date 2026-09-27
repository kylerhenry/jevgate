# Closing a period drops cached balances

## Why

After a period is closed, `GET /v1/reports/balance` keeps serving the cached pre-close figure for up to 60 s, so the dashboard shows a balance the closed books no longer have.

## What

In `src/ledger/services/periods.py`, `close_period` runs the storage layer's post-close hook (`store.after_close`) after the close row is written, so the TTL cache entries for that tenant's balances are invalidated as part of the close.

## Acceptance

- After `close_period(period)`, the next `balance(period)` call reads from the store, not from the cache.
- Closing a period still writes exactly one `period_close` row.
- Closing an already closed period raises `PeriodClosedError`.

## Context

- The cache helper sits in front of store reads and exposes `invalidate(prefix)`.
- The storage facade owns the post-close hook.
