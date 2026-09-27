---
aliases: [bus, audit events]
path: src/ledger/integration/bus.py
---

# Event bus

Introduced by [[0003-event-bus-for-audit]]. Subscribers run after the request's transaction commits, so a failing subscriber never rolls back a posted entry.

## Provides

An in-process publish/subscribe bus for audit, email and webhook side effects, drained after each request.

## Interface

publish(topic, payload)
subscribe(topic, handler)
drain()
