# Make statement imports faster

## Why

Importing a 20,000-row statement takes eleven minutes on the shared tenant, and operators run imports during the working day.

## What

The CSV importer (`src/ledger/services/importer.py`) batches candidate entries and posts them through `post_entry` in groups of 500 inside one transaction per group instead of one transaction per entry. Audit events are still published per entry on the Event bus after each group commits.

## Acceptance

- Imports feel fast enough for the operators.
- The code is clean and easy to maintain.
- We have looked into the slow paths and addressed them.

## Context

- The CSV importer posts matched entries through `post_entry` after matching rules.
- Event bus subscribers run after the request's transaction commits.
