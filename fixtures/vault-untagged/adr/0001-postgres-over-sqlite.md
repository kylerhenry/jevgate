---
status: accepted
---

# ADR 0001: Postgres over SQLite

## Context

Ledger started on SQLite for a single user; multi-tenant hosting needs concurrent writers and row-level tenancy.

## Decision

Ledger uses one Postgres database with a tenant column on every table and reporting views per tenant; SQLite is dropped, including for tests.

## Alternatives

- SQLite file per tenant: no concurrent writers and no shared reporting.
- Redis as a cache tier: rejected in favour of the in-process [[Cache helper]] to keep one runtime dependency.

## Consequences

Tests run against a throwaway Postgres schema; the [[Postgres store]] is the only SQL owner.
