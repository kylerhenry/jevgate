# Publish a period.closed audit event when a period is closed

## Why

Closing a period is the one bookkeeping action with no audit trail. Auditors asked who closed each period and when, and today the answer is only in the request logs.

## What

In `src/ledger/services/periods.py`, `close_period` publishes `period.closed` on the event bus with `{tenant_id, period_id, closed_by}` after the close row is written. Nothing else changes.

## Acceptance

- Closing a period publishes exactly one `period.closed` event carrying tenant_id, period_id and closed_by.
- A failing subscriber does not roll back the close.
- No event is published when the close itself fails.

## Context

- The event bus drains after the request's transaction commits, so subscribers cannot roll back a close.
