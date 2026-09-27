# Cache trial-balance reads in the reporting service

## Why

`GET /v1/reports/trial-balance` runs the same view query for every poll of the dashboard, and the reporting view takes 300 ms for large tenants.

## What

Add a small in-memory dictionary cache with a 60-second TTL inside the reporting service (`src/ledger/services/reporting.py`), keyed by tenant and period, so repeated `v_trial_balance` reads skip the database. Entries expire after 60 seconds and a call to `post_entry` clears the dictionary for that tenant.

## Acceptance

- Two calls to `GET /v1/reports/trial-balance?period=2026-08` within 60 seconds run one view query (`test_reporting.py::test_trial_balance_cached`).
- After `post_entry` for the tenant, the next call runs the view query again.

## Context

- Reports read from the reporting views, never from the entries table.
- The reporting service lives in the service layer.
