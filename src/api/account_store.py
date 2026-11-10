"""Account-local protection and transactional persistent round-robin selection."""
from __future__ import annotations

import time

from .alert_store import account_exception


async def initialize_accounts(db):
    cursor = await db.execute("PRAGMA table_info(accounts)")
    columns = {row[1] for row in await cursor.fetchall()}
    for name, declaration in (
        ("state", "TEXT NOT NULL DEFAULT 'normal'"),
        ("reason", "TEXT"),
        ("until", "REAL"),
        ("proxy_url", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            await db.execute(f"ALTER TABLE accounts ADD COLUMN {name} {declaration}")
    await db.execute("CREATE TABLE IF NOT EXISTS account_rotation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_account_id TEXT)")
    await db.execute("INSERT OR IGNORE INTO account_rotation VALUES (1, NULL)")


class AccountStoreMixin:
    async def get_account_proxy(self, account_id):
        async with self._queue_connection() as db:
            cursor = await db.execute("SELECT proxy_url FROM accounts WHERE account_id=?", (account_id,))
            row = await cursor.fetchone()
            return row[0] if row else ""

    async def set_account_proxy(self, account_id, proxy_url):
        async with self._queue_connection() as db:
            cursor = await db.execute(
                "UPDATE accounts SET proxy_url=?, updated_at=? WHERE account_id=? AND state<>'deleting'",
                (proxy_url, time.time(), account_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def _select_account_in_transaction(self, db, account_id=None, *, exclude=None):
        now = time.time()
        await db.execute("UPDATE accounts SET state='normal', reason=NULL, until=NULL, updated_at=? WHERE state='cooldown' AND until<=?", (now, now))
        cursor = await db.execute("""
            SELECT a.account_id, c.version FROM accounts a
            JOIN cookie_versions c ON c.account_id=a.account_id AND c.is_active=1
            WHERE a.enabled=1 AND a.state='normal'
              AND (? IS NULL OR a.account_id=?)
              AND (? IS NULL OR a.account_id<>?)
            ORDER BY CASE WHEN a.account_id > COALESCE(
                (SELECT last_account_id FROM account_rotation WHERE singleton=1), '') THEN 0 ELSE 1 END,
                a.account_id LIMIT 1
        """, (account_id, account_id, exclude, exclude))
        row = await cursor.fetchone()
        if row is None:
            return None
        if account_id is None:
            await db.execute("UPDATE account_rotation SET last_account_id=? WHERE singleton=1", (row[0],))
        await db.execute("UPDATE accounts SET last_selected_at=?, updated_at=? WHERE account_id=?", (now, now, row[0]))
        return str(row[0]), int(row[1])

    async def get_account_state(self, account_id):
        async with self._queue_connection() as db:
            rows = await db.execute_fetchall("SELECT account_id, display_name, enabled, state, reason, until, proxy_url FROM accounts WHERE account_id=?", (account_id,))
            if not rows:
                return None
            state = dict(rows[0])
            if state["state"] == "cooldown" and state["until"] <= time.time():
                state.update(state="normal", reason=None, until=None)
            return state

    async def pause_account(self, account_id, reason):
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("UPDATE accounts SET state='paused', reason=?, until=NULL, updated_at=? WHERE account_id=? AND state<>'deleting'", (reason, time.time(), account_id))
            if cursor.rowcount:
                await account_exception(db, account_id, reason)
            await db.commit()

    async def cooldown_account(self, account_id, reason, until):
        async with self._queue_connection() as db:
            await db.execute("UPDATE accounts SET state='cooldown', reason=?, until=MAX(COALESCE(until,0),?), updated_at=? WHERE account_id=? AND state NOT IN ('paused','deleting')", (reason, until, time.time(), account_id))
            await db.commit()
