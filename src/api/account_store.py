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
        ("user_agent", "TEXT NOT NULL DEFAULT ''"),
        ("periodic_test_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("periodic_test_url", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            await db.execute(f"ALTER TABLE accounts ADD COLUMN {name} {declaration}")
    await db.execute("CREATE TABLE IF NOT EXISTS account_rotation (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_account_id TEXT)")
    await db.execute("INSERT OR IGNORE INTO account_rotation VALUES (1, NULL)")


class AccountStoreMixin:
    async def set_account_periodic_test(self, account_id, enabled, product_url):
        async with self._queue_connection() as db:
            cursor = await db.execute("""UPDATE accounts SET periodic_test_enabled=?,
                periodic_test_url=COALESCE(?, periodic_test_url), updated_at=?
                WHERE account_id=? AND state<>'deleting'""",
                (int(enabled), product_url, time.time(), account_id))
            await db.commit()
            return cursor.rowcount > 0

    async def periodic_test_candidates(self):
        async with self._queue_connection() as db:
            cursor = await db.execute("""SELECT account_id, proxy_url, updated_at, periodic_test_url
                FROM accounts WHERE periodic_test_enabled=1 AND state='paused'""")
            return [dict(row) for row in await cursor.fetchall()]

    async def recover_product_test(self, account, *, periodic=False):
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("""UPDATE accounts SET state='normal', reason=NULL,
                until=NULL, updated_at=? WHERE account_id=? AND updated_at=?
                AND state IN ('paused', 'cooldown')
                AND (?=0 OR (state='paused' AND periodic_test_enabled=1))""",
                (time.time(), account["account_id"], account["updated_at"], int(periodic)))
            if cursor.rowcount:
                await db.execute("UPDATE account_alert_cycles SET active=0 WHERE account_id=?",
                                 (account["account_id"],))
            await db.commit()
            return cursor.rowcount > 0

    async def get_account_user_agent(self, account_id):
        async with self._queue_connection() as db:
            cursor = await db.execute("SELECT user_agent FROM accounts WHERE account_id=?", (account_id,))
            row = await cursor.fetchone()
            return row[0] if row else ""

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

    async def _select_account_in_transaction(
        self, db, account_id=None, *, exclude=None, max_running=None,
        prefer_idle=False, select=True,
    ):
        now = time.time()
        await db.execute("UPDATE accounts SET state='normal', reason=NULL, until=NULL, updated_at=? WHERE state='cooldown' AND until<=?", (now, now))
        cursor = await db.execute("""
            SELECT a.account_id, c.version FROM accounts a
            JOIN cookie_versions c ON c.account_id=a.account_id AND c.is_active=1
            WHERE a.enabled=1 AND a.state='normal'
              AND (? IS NULL OR a.account_id=?)
              AND (? IS NULL OR a.account_id<>?)
              AND (? IS NULL OR (SELECT COUNT(*) FROM queue_tasks q
                   WHERE q.account_id=a.account_id AND q.status='running') < ?)
            ORDER BY CASE WHEN ? THEN (SELECT COUNT(*) FROM queue_tasks q
                WHERE q.account_id=a.account_id AND q.status='running') ELSE 0 END,
                CASE WHEN a.account_id > COALESCE(
                (SELECT last_account_id FROM account_rotation WHERE singleton=1), '') THEN 0 ELSE 1 END,
                a.account_id LIMIT 1
        """, (account_id, account_id, exclude, exclude, max_running, max_running, prefer_idle))
        row = await cursor.fetchone()
        if row is None:
            return None
        if not select:
            return str(row[0]), int(row[1])
        if account_id is None:
            await db.execute("UPDATE account_rotation SET last_account_id=? WHERE singleton=1", (row[0],))
        await db.execute("UPDATE accounts SET last_selected_at=?, updated_at=? WHERE account_id=?", (now, now, row[0]))
        return str(row[0]), int(row[1])

    async def get_account_state(self, account_id):
        async with self._queue_connection() as db:
            rows = await db.execute_fetchall("SELECT account_id, display_name, enabled, state, reason, until, proxy_url, periodic_test_enabled, periodic_test_url, updated_at FROM accounts WHERE account_id=?", (account_id,))
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
