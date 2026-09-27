---
status: accepted
---

# ADR 0002: Server-rendered invoices

## Context

Customers download invoices as PDF and view them as HTML; the two must match.

## Decision

Invoices are rendered on the server to HTML and PDF by the [[Invoice renderer]]; there is no client-side invoice template.

## Alternatives

- A JavaScript front end rendering invoices from the JSON API: rejected because PDFs must match the HTML byte for byte.
- A third-party invoicing SaaS: rejected on data residency.

## Consequences

The renderer is the only consumer of the reporting views outside reports.
