---
tags: [jevgate/component]
aliases: [store, db, database]
path: src/ledger/storage/postgres.py
provides: All SQL for entries, periods, tenants and reporting views; the only module that opens a database connection.
interface: "post_entry(entry) -> EntryId; entries(period) -> list[Entry]; views.balance(period)"
---

# Postgres store

Chosen in [[0001-postgres-over-sqlite]]. Amounts are integer minor units (see the [[architecture]] rules). The [[Cache helper]] sits in front of reads.
