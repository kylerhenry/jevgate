---
aliases: [cache, TTL cache]
path: src/ledger/storage/cache.py
provides: A TTL cache in front of Postgres store reads, keyed by query name and parameters, invalidated on every write through post_entry.
interface: "get(key) -> value | None; set(key, value, ttl_s=60); invalidate(prefix)"
---

# Cache helper

Wraps [[Postgres store]] reads. Chosen over a Redis sidecar in [[0001-postgres-over-sqlite]]. Services call it through the store facade; nothing above the storage layer imports it.
