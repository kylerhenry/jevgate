---
status: proposed
---

# ADR 0003: Event bus for audit and email

## Context

Every service was calling the mailer and writing audit rows inline, so each needed both dependencies.

## Decision

Audit records and outbound email are produced by subscribers on the in-process [[Event bus]], drained after commit; services never call the mailer or the audit table directly.

## Alternatives

- Inline calls from each service: rejected because every service would need the mailer and audit dependencies.
- A message broker such as RabbitMQ: rejected as infrastructure the deployment does not have.

## Consequences

Side effects become observable in one place; a subscriber failure is logged, not raised.
