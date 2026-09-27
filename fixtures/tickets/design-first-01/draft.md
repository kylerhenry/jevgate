# Close a period that still has unmatched imports

## Why

`ledger close` refuses a period while any imported candidate entry is unmatched, so operators delete the candidates by hand and lose the audit trail.

## What

Decide how unmatched candidates are handled at close time: they could be rejected with a list, carried into the next period, or posted to a suspense account. Implement whichever option is chosen across the CSV importer, the close service and the Ledger CLI, and record the choice as an ADR.

## Acceptance

- `ledger close --period 2026-08` succeeds on the test tenant that has unmatched candidates.
- The chosen behaviour is covered by a test in `test_close.py`.

## Context

- Use cases such as posting entries and closing periods live in the service layer.
- The CSV importer posts matched entries through `post_entry`.
