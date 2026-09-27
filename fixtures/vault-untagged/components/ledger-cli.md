---
aliases: [cli, ledger command]
path: src/ledger/cli.py
provides: Command-line entry point for posting entries, closing periods and running imports against a local or remote Ledger.
interface: "ledger post|close|import|report [--tenant T]"
---

# Ledger CLI

Thin wrapper over the same services the [[API router]] calls; output is JSON with --json.
