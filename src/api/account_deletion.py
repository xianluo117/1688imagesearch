
"""Transactional account retirement; historical task identities remain intact."""
import json
import time


class AccountDeletingError(ValueError):
    pass


async def migrate_account_deletion(db):
    # Rebuild the sole NOT NULL cookie reference before installing queue triggers.
    cursor = await db.execute("PRAGMA table_info(product_tasks)")
    if any(row[1] == "cookie_version" and row[3] for row in await cursor.fetchall()):
        await db.execute("DROP TRIGGER IF EXISTS product_tasks_terminal_immutable")
        await db.execute("DROP TRIGGER IF EXISTS product_tasks_queue_sync")
        await db.execute("""CREATE TABLE product_tasks_nullable (
            task_id TEXT PRIMARY KEY, upload_task_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
            account_id TEXT NOT NULL DEFAULT 'default', cookie_version INTEGER,
            result_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
            error_code TEXT, error_message TEXT, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, started_at REAL, finished_at REAL,
            FOREIGN KEY(upload_task_id) REFERENCES upload_tasks(task_id),
            FOREIGN KEY(cookie_version) REFERENCES cookie_versions(version))""")
        columns = "task_id,upload_task_id,status,account_id,cookie_version,result_json,cancel_requested,error_code,error_message,created_at,updated_at,started_at,finished_at"
        await db.execute(f"INSERT INTO product_tasks_nullable ({columns}) SELECT {columns} FROM product_tasks")
        await db.execute("DROP TABLE product_tasks")
        await db.execute("ALTER TABLE product_tasks_nullable RENAME TO product_tasks")
        await db.execute("CREATE INDEX idx_product_tasks_status_created ON product_tasks(status, created_at)")
        await db.execute("CREATE INDEX idx_product_tasks_upload ON product_tasks(upload_task_id, created_at)")
    for table in ("upload_tasks", "product_tasks"):
        await db.execute(f"DROP TRIGGER IF EXISTS {table}_terminal_immutable")
        cursor = await db.execute(f"PRAGMA table_info({table})")
        unchanged = ' AND '.join(f'NEW.{row[1]} IS OLD.{row[1]}' for row in await cursor.fetchall() if row[1] != 'cookie_version')
        await db.execute(f"""CREATE TRIGGER {table}_terminal_immutable BEFORE UPDATE ON {table}
            WHEN OLD.status IN ('succeeded','failed','cancelled') AND NOT (
                NEW.cookie_version IS NULL AND OLD.cookie_version IS NOT NULL
                AND {unchanged})
            BEGIN SELECT RAISE(IGNORE); END""")


class AccountDeletionMixin:
    async def _bind_waiting_task(self, db, row, selected):
        account_id, version = selected or ("", None)
        payload = json.loads(row["payload_json"])
        payload["cookie_version"] = version
        if row["kind"] == "product":
            payload["reupload_required"] = True
        await db.execute("UPDATE queue_tasks SET account_id=?, payload_json=? WHERE task_id=?",
                         (account_id, json.dumps(payload, ensure_ascii=False), row["task_id"]))
        if row["kind"] != "sku":
            table = "upload_tasks" if row["kind"] == "upload" else "product_tasks"
            await db.execute(f"UPDATE {table} SET account_id=?, cookie_version=? WHERE task_id=?",
                             (account_id, version, row["task_id"]))

    async def _reassign_waiting(self, db):
        rows = await db.execute_fetchall("""SELECT q.* FROM queue_tasks q WHERE q.status='queued'
            AND (q.account_id='' OR EXISTS (SELECT 1 FROM accounts a
                WHERE a.account_id=q.account_id AND a.state='deleting')) ORDER BY q.seq""")
        for row in rows:
            await self._bind_waiting_task(db, row, await self._select_account_in_transaction(db))

    async def _finalize_deletions(self, db):
        rows = await db.execute_fetchall("""SELECT account_id FROM accounts a WHERE state='deleting'
            AND NOT EXISTS (SELECT 1 FROM queue_tasks q WHERE q.account_id=a.account_id
                AND q.status IN ('queued','running'))""")
        for row in rows:
            account_id = row[0]
            for table in ("upload_tasks", "product_tasks"):
                await db.execute(f"UPDATE {table} SET cookie_version=NULL WHERE cookie_version IN (SELECT version FROM cookie_versions WHERE account_id=?)", (account_id,))
            await db.execute("DELETE FROM cookie_versions WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM accounts WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM account_alert_cycles WHERE account_id=?", (account_id,))

    async def delete_account(self, account_id):
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("UPDATE accounts SET state='deleting', updated_at=? WHERE account_id=?", (time.time(), account_id))
            if not cursor.rowcount:
                return None
            await self._reassign_waiting(db)
            await self._finalize_deletions(db)
            cursor = await db.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,))
            state = "deleting" if await cursor.fetchone() else "deleted"
            await db.commit()
            return {"account_id": account_id, "state": state}

    async def recover_account_deletions(self):
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await self._reassign_waiting(db)
            await self._finalize_deletions(db)
            await db.commit()
