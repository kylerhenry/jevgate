# Add a `--dry-run` flag to `ledger import`

## Why

Operators importing a bank statement cannot see which entries the matching rules will post until the entries are in the books. A wrong rule then needs a correcting posting for every affected entry, which costs about an hour per statement.

## What

`import_statement` in the CSV importer (`src/ledger/services/importer.py`) gains a `dry_run: bool = False` parameter. When it is true, the importer runs the matching rules and fills the `ImportReport` with the candidate entries. It does not call `post_entry` and publishes no audit event on the Event bus.

The Ledger CLI (`src/ledger/cli.py`) gains `ledger import --dry-run <file>`. It passes `dry_run=True` and prints the report. With `--json` the output is the same `ImportReport` shape the API returns.

Out of scope: the HTTP import endpoint is unchanged, and editing matching rules stays in the existing `ledger rules` commands.

### Behaviour

`ledger import --dry-run statement.csv` prints one line per candidate entry (account, amount in minor units, matched rule) and exits 0. The books are unchanged afterwards.

## Acceptance

- `ledger import --dry-run tests/data/statement.csv` prints the candidate entries and exits 0.
- After a dry run, `ledger report trial-balance --json --period 2026-08` returns the same output as before the run.
- `test_importer.py::test_dry_run_posts_nothing` passes: `post_entry` is not called and no audit event is published.
- `ledger import tests/data/statement.csv` without the flag still posts the matched entries; `test_import_posts_entries` stays green.

## Context

- The CSV importer (`src/ledger/services/importer.py`) posts matched entries through `post_entry` after applying the per-tenant matching rules.
- Each imported entry publishes an audit event on the Event bus.
- The Ledger CLI is a thin wrapper over the same services the API router calls; `--json` output follows the API shapes.
