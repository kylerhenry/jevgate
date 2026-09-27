---
tags: [jevgate/architecture]
---

# Ledger architecture

Ledger is a small bookkeeping web service: a JSON API and a CLI in front of a Postgres store, with server-rendered invoices. Requests flow inward through the layers below and nothing in a lower layer imports an upper one. Component notes: [[Cache helper]], [[Postgres store]], [[API router]]; decisions: [[0001-postgres-over-sqlite]]. The [[Billing engine]] is planned and has no note yet.

## Layers

### Interface layer #jevgate/layer

HTTP handlers ([[API router]]), the [[Ledger CLI]] and the [[Invoice renderer]]: parse input, call a service, format output. No business rules.

### Service layer #jevgate/layer

Use cases such as posting entries, closing periods and importing statements ([[CSV importer]]). Owns validation and the double-entry invariant.

### Storage layer #jevgate/layer

The [[Postgres store]] and the [[Cache helper]] in front of it. The only layer that contains SQL.

### Integration layer #jevgate/layer

Outbound side effects: the [[Event bus]] for audit events, email and bank feeds.

## Rules

- Interface code never calls the store directly; it goes through a service. #jevgate/rule
- Every write to the ledger goes through `post_entry`, which enforces double entry. #jevgate/rule
- The storage layer is the only layer that contains SQL. #jevgate/rule
- Cross-cutting side effects (audit, email) are published on the event bus, not called inline. #jevgate/rule
- Reports read from the reporting views, never from the entries table. #jevgate/rule
- Money is handled as integer minor units; a float never reaches the storage layer. #jevgate/rule
- (draft) Reporting may bypass the cache while the view migration is unfinished. #jevgate/ignore

Rules are checked one by one by the ticket gate and the delivery gate.
