---
tags: [jevgate/component]
aliases: [router, http api]
path: src/ledger/api/router.py
provides: Maps HTTP routes to service calls and turns service errors into JSON error responses.
interface: "route(method, path) decorator; error_response(exc) -> Response"
---

# API router

Every handler is three lines: parse, call a service, render. Handlers never import [[Postgres store]]; see the rules in [[architecture]]. Authentication is applied by the [[Auth]] middleware before a handler runs.
