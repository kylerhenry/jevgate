# Expose the tenant balance over the API

## Why

The dashboard computes balances client-side from `/v1/entries`, which is slow for large tenants and wrong for closed periods because it ignores closing adjustments.

## What

Add `GET /v1/reports/balance?period=<id>` to the API router. The handler parses the period id, calls `reports.balance(period)` and returns `{period_id, balance_minor, currency}`; an unknown period is a 404 with code `period_not_found`.

## Acceptance

- `GET /v1/reports/balance?period=<id>` returns the balance computed by the reports service from the reporting view.
- An unknown period returns 404 with error code `period_not_found`.
- The handler is three lines: parse, call the service, render.

## Context

- Handlers never import the store; reports read from the reporting views, never from the entries table.
