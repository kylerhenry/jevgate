# Log the row count of each statement import

## Why

Support cannot tell from the logs how many rows an import processed when a tenant reports missing entries; today an import leaves no log line at all.

## What

In `src/ledger/services/importer.py`, `import_statement` logs one structured line per import after the import commits, with the tenant id, the file name, the rows parsed and the entries posted.

## Acceptance

- Every `import_statement` call logs one line containing the tenant id, file name, rows parsed and entries posted.
- The line is emitted after the import has committed.
- Existing import behaviour is unchanged.

## Context

- Log lines are structured key=value pairs.
