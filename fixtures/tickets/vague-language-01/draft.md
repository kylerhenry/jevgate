# Fix the importer

## Why

The importer sometimes does the wrong thing with some statements and users get confused by it.

## What

Update the CSV importer (`src/ledger/services/importer.py`) so it handles them properly. When it sees one of those rows it should skip it or fix it, depending on which is better, and then let the other one know. This should be done for the bank feeds too.

## Acceptance

- `test_importer.py` passes with the new sample statements added under `tests/data/`.
- The importer no longer does the wrong thing with the affected statements.

## Context

- The CSV importer parses bank statement CSV files into candidate entries and posts them through `post_entry`.
