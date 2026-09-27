---
tags: [jevgate/component]
aliases: [importer, bank import]
path: src/ledger/services/importer.py
provides: Parses bank statement CSV files into candidate entries and posts them through post_entry after matching rules.
interface: "import_statement(tenant, file) -> ImportReport"
---

# CSV importer

Matching rules are per tenant and stored in the [[Postgres store]]. Each imported entry publishes an audit event on the [[Event bus]].
