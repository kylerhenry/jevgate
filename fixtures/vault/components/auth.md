---
tags: [jevgate/component]
aliases: [authentication, session middleware]
path: src/ledger/api/auth.py
provides: Session cookie and API token authentication as router middleware; loads the current tenant.
interface: "require_user(handler); current_tenant() -> Tenant"
---

# Auth

Tokens are hashed and looked up in the [[Postgres store]] `api_tokens` table. No authorization rules live here; services check permissions.
