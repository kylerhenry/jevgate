---
area: architecture
project: other
---

# Warehouse architecture

The warehouse project shares this vault but not the Ledger code. Its jobs read Ledger exports, never the live database.

## Rules

- Warehouse jobs never write to the ledger database.
