# Multi-currency support

## Why

Tenants with customers abroad keep a second set of books in a spreadsheet because Ledger stores one currency per tenant.

## What

Add a `currency` column to `entries` and `periods` with a migration, and a `rates` table filled nightly by a new scheduler service that fetches ECB rates. The CSV importer detects the statement currency and converts amounts. `/v1/entries`, `/v1/periods` and `/v1/reports/<name>` gain `currency` fields and a `?currency=` filter. The Ledger CLI mirrors the API changes. The Invoice renderer shows original and converted amounts on HTML and PDF. The reporting views `v_balance` and `v_trial_balance` gain per-currency rows and a consolidated row. A new admin page in the API lets an operator edit rates by hand. The Event bus publishes a `rate.updated` event consumed by the email subscriber.

### Behaviour

Every screen, report, export and import handles more than one currency.

## Acceptance

- Migration `0012_currency.sql` applies and rolls back cleanly on the test schema.
- `ledger import` on `tests/data/statement-eur.csv` posts entries in EUR with converted minor units.
- `GET /v1/reports/trial-balance?currency=EUR` returns EUR rows only.
- The invoice PDF for a mixed-currency period shows both amounts per line.
- The rates admin page saves an edited rate and publishes `rate.updated`.

## Context

- Money is handled as integer minor units; a float never reaches the storage layer.
- Migrations are numbered SQL files applied by `ledger migrate`.
