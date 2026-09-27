# Retry failed webhook deliveries from the Event bus

## Why

A webhook subscriber that fails once loses the event: the subscriber failure is logged and the customer's system never receives the posting.

## What

The Event bus (`src/ledger/integration/bus.py`) records a failed webhook delivery in a `webhook_retries` table through the Postgres store and retries it up to three times with a one-minute gap from `drain()`. After the third failure the event is published on the `webhook.dead` topic for the email subscriber.

## Acceptance

- `test_bus.py::test_webhook_retry` passes: a subscriber that fails twice and then succeeds delivers the payload once.
- After three failures a `webhook.dead` event is published with the original payload.

## Context

- The Postgres store (`src/ledger/storage/postgres.py`) holds all SQL and is the only module that opens a database connection.
