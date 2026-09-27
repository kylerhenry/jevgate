#jevgate/data

# Data

Entries are immutable rows in `entries` (tenant_id, period_id, account, amount_minor, currency, posted_at). Periods close by inserting a `period_close` row; nothing is updated in place.

Reporting views (`v_balance`, `v_trial_balance`) are refreshed by trigger. Migrations are numbered SQL files applied by `ledger migrate`; a migration never rewrites history. The [[Postgres store]] owns all of this.
