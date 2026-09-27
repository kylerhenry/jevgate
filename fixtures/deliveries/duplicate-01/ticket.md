# Cache balance lookups for 60 seconds

## Why

`GET /v1/reports/balance` recomputes the balance view on every call. Dashboards poll it every few seconds and the store shows it as the most expensive query by a wide margin.

## What

Read `balance(period)` through the storage layer's TTL cache (`src/ledger/storage/cache.py`) keyed by tenant and period with a 60 s TTL. Writes already invalidate the cache through `post_entry`, so no invalidation code is needed in the reports service.

## Acceptance

- Two `balance(period)` calls within 60 s hit the store once.
- A `post_entry` for the same tenant makes the next `balance(period)` call hit the store again.
- The cached value equals the uncached value.

## Context

- The cache helper is keyed by query name and parameters and is invalidated on every write through `post_entry`.
