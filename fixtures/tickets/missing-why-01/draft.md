# Add a `--dry-run` flag to `ledger import`

## What

`import_statement` in the CSV importer (`src/ledger/services/importer.py`) gains a `dry_run` parameter. When it is true the importer fills the `ImportReport` but does not call `post_entry`. The Ledger CLI gains `ledger import --dry-run <file>`.

## Acceptance

- `ledger import --dry-run tests/data/statement.csv` prints the candidate entries and exits 0.
- `test_importer.py::test_dry_run_posts_nothing` passes.

## Context

- The CSV importer posts matched entries through `post_entry`.
