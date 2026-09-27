# Serve account balances straight from the router

## Why

`GET /v1/reports/balance` takes 400 ms for large tenants because it goes through the reporting service and the cache; the mobile client polls it every few seconds.

## What

Add `GET /v1/accounts/<id>/balance` to the API router (`src/ledger/api/router.py`). The handler opens a connection through the Postgres store and runs `SELECT SUM(amount_minor) FROM entries WHERE account = %s AND tenant_id = %s` itself, skipping the service layer and the cache to keep latency low. The result is returned as `{balance: <minor units>}`.

### Behaviour

The endpoint answers in under 50 ms for the test tenant.

## Acceptance

- `GET /v1/accounts/1200/balance` returns `{balance: 125000}` for the test tenant.
- `test_router.py::test_account_balance_latency` passes with p95 under 50 ms over 100 requests.

## Context

- The API router maps HTTP routes to service calls.
- The Postgres store is the only module that opens a database connection.
