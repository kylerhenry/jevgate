# Add a `--dry-run` flag to `ledger import`

## Why

In order to leverage a more robust and seamless import experience, it is important to note that operators who are importing a bank statement are currently unable to comprehensively ascertain which entries the various matching rules will ultimately post until such time as the entries have already been persisted into the books, which is problematic.

## What

We should utilize a new `dry_run` parameter on `import_statement` in the CSV importer (`src/ledger/services/importer.py`) in order to ensure that, when it is enabled, the importer will holistically execute the matching rules and populate the `ImportReport` with the candidate entries while streamlining the flow so that `post_entry` is not invoked and no audit event is published on the Event bus. Additionally, the Ledger CLI (`src/ledger/cli.py`) should be augmented with a `--dry-run` option that seamlessly passes `dry_run=True` and renders the report, and it's worth noting that with `--json` the output should be the same `ImportReport` shape that the API returns.

## Acceptance

- `ledger import --dry-run tests/data/statement.csv` prints the candidate entries and exits 0.
- `test_importer.py::test_dry_run_posts_nothing` passes: `post_entry` is not called and no audit event is published.

## Context

- The CSV importer posts matched entries through `post_entry` after applying the per-tenant matching rules.
- Each imported entry publishes an audit event on the Event bus.
