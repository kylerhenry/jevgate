# Import statements from the bank feed automatically

## Why

Operators download each statement from the bank portal and upload it by hand every morning, and a missed day leaves the books a day behind.

## What

Add a nightly import that pulls the day's statement from the bank feed and runs it through the CSV importer (`src/ledger/services/importer.py`) for every tenant that has a feed configured.

## Acceptance

- Statements from the bank feed are imported every night for every configured tenant.

## Context

- The CSV importer posts matched entries through `post_entry` after matching rules.
- Bank feeds belong to the integration layer.
