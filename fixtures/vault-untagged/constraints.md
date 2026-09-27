---
area: constraints
---

# Constraints

Non-functional rules every change must keep.

## Constraints

- Runtime dependencies are Postgres and the Python standard library plus `psycopg`; no other services in production.
- Every request finishes in under 300 ms at the 95th percentile with 10,000 entries per tenant.
- Tenant data never crosses a tenant boundary: every query carries the tenant id.
- Secrets come from the environment; nothing secret is written to logs or the repo.
