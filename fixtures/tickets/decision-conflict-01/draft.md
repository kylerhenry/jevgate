# Add a Redis cache tier for report reads

## Why

`GET /v1/reports/trial-balance` runs the reporting view for every dashboard poll, and the in-process cache is emptied on every deploy.

## What

Run a Redis sidecar next to the API and cache `v_trial_balance` results in it, keyed by tenant and period with a 60-second TTL. The reporting service reads Redis first and falls back to the Postgres store; `post_entry` deletes the tenant's keys. The Cache helper is left in place for other reads.

## Acceptance

- Two calls to `GET /v1/reports/trial-balance?period=2026-08` within 60 seconds run one view query (`test_reporting.py::test_trial_balance_redis`).
- After a deploy, the first call is served from Redis.

## Context

- Reports read from the reporting views, never from the entries table.
- The Cache helper is a TTL cache in front of Postgres store reads.
