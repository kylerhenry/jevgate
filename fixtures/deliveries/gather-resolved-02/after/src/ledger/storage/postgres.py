"""Postgres store: every SQL statement in Ledger lives here."""
from __future__ import annotations

from ledger.storage import cache
from ledger.types import Entry, EntryId, Period, User

from ._conn import connect


def period(tenant_id: str, period_id: str) -> Period | None:
    """Load one period, or None when the tenant has no such period."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, tenant_id, currency, closed FROM periods WHERE tenant_id = %s AND id = %s",
            (tenant_id, period_id),
        ).fetchone()
    return Period(*row) if row else None


def close_period(period: Period, user: User) -> None:
    """Write the period_close row; nothing is updated in place."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO period_close (tenant_id, period_id, closed_by) VALUES (%s, %s, %s)",
            (period.tenant_id, period.id, user.id),
        )


def after_close(period: Period) -> None:
    """Post-close hook: drop every cached balance for the period's tenant."""
    cache.invalidate(f"balance:{period.tenant_id}:")
    cache.invalidate(f"trial_balance:{period.tenant_id}:")


def post_entry(entry: Entry) -> EntryId:
    """Insert one balanced entry and invalidate the tenant's cached reads."""
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO entries (tenant_id, period_id, account, amount_minor, currency) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (entry.tenant_id, entry.period_id, entry.account, entry.amount_minor, entry.currency),
        ).fetchone()
    cache.invalidate(f"balance:{entry.tenant_id}:")
    return EntryId(row[0])
