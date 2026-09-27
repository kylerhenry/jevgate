---
tags: [jevgate/interfaces, jevgate/project/ledger]
---

# Interfaces

## HTTP API

`/v1/entries`, `/v1/periods`, `/v1/invoices/<id>` and `/v1/reports/<name>`; JSON in and out, errors as `{error: {code, message}}`. Served by the [[API router]].

## CLI

`ledger post|close|import|report`, `--json` mirrors the API shapes ([[Ledger CLI]]).

## Compatibility

The API is versioned by path; removing or renaming a field needs a new version. The CLI's `--json` output follows the API shapes.
