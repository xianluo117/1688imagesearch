from __future__ import annotations

import asyncio
import tempfile
import time
from collections import Counter
from unittest.mock import patch

import aiosqlite
from api.queue_store import IdempotencyConflict
import unittest
from pathlib import Path

from api.database import Database


class MultiAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "accounts.db")
        await self.db.initialize()
        await self.db.ensure_account("account-a", "账号 A")
        await self.db.ensure_account("account-b", "账号 B")
        await self.db.store_cookie(b"a", cookie_count=1, exported_at=None, account_id="account-a")
        await self.db.store_cookie(b"b", cookie_count=1, exported_at=None, account_id="account-b")

    async def test_round_robin_account_selection(self):
        selected = [await self.db.select_account() for _ in range(4)]
        self.assertEqual(selected, ["account-a", "account-b", "account-a", "account-b"])

    async def test_cookie_versions_are_isolated_per_account(self):
        a = await self.db.get_active_cookie("account-a")
        b = await self.db.get_active_cookie("account-b")
        self.assertEqual(a[1], b"a")
        self.assertEqual(b[1], b"b")
        self.assertNotEqual(a[0], b[0])

    async def test_sku_task_binds_selected_cookie_version(self):
        task_id = await self.db.create_sku_task("sku", account_id="account-a")
        task = await self.db.get_queue_task(task_id)
        self.assertEqual(task.account_id, "account-a")
        self.assertEqual(task.payload["cookie_version"], (await self.db.get_active_cookie("account-a"))[0])


    async def test_concurrent_mixed_rotation_and_restart(self):
        await self.db.store_cookie(b"c", cookie_count=1, exported_at=None, account_id="account-c")
        ids = await asyncio.gather(*(
            self.db.create_sku_task(str(i)) if i % 2 else self.db.create_upload_task(str(i))
            for i in range(18)
        ))
        tasks = sorted(await asyncio.gather(*(self.db.get_queue_task(i) for i in ids)), key=lambda t: t.seq)
        self.assertEqual([t.account_id for t in tasks], ["account-a", "account-b", "account-c"] * 6)
        self.assertEqual(Counter(t.account_id for t in tasks), {"account-a": 6, "account-b": 6, "account-c": 6})
        await self.db.initialize()
        next_id = await self.db.create_sku_task("next")
        self.assertEqual((await self.db.get_queue_task(next_id)).account_id, "account-a")

    async def test_idempotency_cookie_update_explicit_and_capacity_do_not_rotate(self):
        first = await self.db.create_sku_task("same", idempotency_key="key")
        original = await self.db.get_queue_task(first)
        await self.db.store_cookie(b"new", cookie_count=1, exported_at=None, account_id="account-a")
        replay = await asyncio.gather(*(self.db.create_sku_task("same", idempotency_key="key") for _ in range(8)))
        self.assertEqual(replay, [first] * 8)
        self.assertEqual(await self.db.get_queue_task(first), original)
        with self.assertRaises(IdempotencyConflict):
            await self.db.create_sku_task("different", idempotency_key="key")
        await self.db.create_sku_task("explicit", account_id="account-a")
        self.assertIsNone(await self.db.create_sku_task("full", max_active=2))
        next_id = await self.db.create_sku_task("next")
        self.assertEqual((await self.db.get_queue_task(next_id)).account_id, "account-b")

    async def test_idempotency_includes_explicit_account_not_dynamic_version(self):
        first = await self.db.create_sku_task("same", account_id="account-a", idempotency_key="explicit")
        await self.db.store_cookie(b"new", cookie_count=1, exported_at=None, account_id="account-a")
        self.assertEqual(await self.db.create_sku_task("same", account_id="account-a", idempotency_key="explicit"), first)
        for account in ("account-b", None):
            with self.assertRaises(IdempotencyConflict):
                await self.db.create_sku_task("same", account_id=account, idempotency_key="explicit")

    async def test_head_skip_busy_paused_and_recovery_preserves_account_order(self):
        first = await self.db.create_sku_task("a1", account_id="account-a")
        second = await self.db.create_sku_task("a2", account_id="account-a")
        third = await self.db.create_sku_task("b1", account_id="account-b")
        self.assertEqual((await self.db.claim_next_task(max_running=3, max_account_running=1)).task_id, first)
        self.assertEqual((await self.db.claim_next_task(max_running=3, max_account_running=1)).task_id, third)
        await self.db.finish_queue_task(first, status="cancelled")
        await self.db.pause_account("account-a", "risk")
        self.assertIsNone(await self.db.claim_next_task(max_running=3, max_account_running=1))
        await self.db.store_cookie(b"old", cookie_count=1, exported_at=None, account_id="account-a")
        self.assertEqual((await self.db.get_account_state("account-a"))["state"], "paused")
        await self.db.store_cookie(b"ok", cookie_count=1, exported_at=None, account_id="account-a", confirmed_login=True)
        self.assertEqual((await self.db.claim_next_task(max_running=3, max_account_running=1)).task_id, second)

    async def test_recovery_atomic_isolated_and_cooldown_expires(self):
        for account in ("account-a", "account-b"):
            await self.db.pause_account(account, "risk")
        original = await self.db.get_active_cookie("account-a")
        execute = aiosqlite.Connection.execute

        def fail_recovery(connection, sql, parameters=()):
            if "SET state='normal'" in sql:
                raise RuntimeError("save failed")
            return execute(connection, sql, parameters)

        with patch.object(aiosqlite.Connection, "execute", fail_recovery):
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                await self.db.store_cookie(b"new", cookie_count=1, exported_at=None, account_id="account-a", confirmed_login=True)
        self.assertEqual(await self.db.get_active_cookie("account-a"), original)
        self.assertEqual((await self.db.get_account_state("account-a"))["state"], "paused")
        await self.db.store_cookie(b"ok", cookie_count=1, exported_at=None, account_id="account-a", confirmed_login=True)
        self.assertEqual((await self.db.get_account_state("account-b"))["state"], "paused")
        await self.db.cooldown_account("account-a", "rate", time.time() + 60)
        self.assertIsNone(await self.db.create_sku_task("none"))
        await self.db.initialize()
        self.assertEqual((await self.db.get_account_state("account-b"))["state"], "paused")
        with patch("api.account_store.time.time", return_value=time.time() + 120):
            task_id = await self.db.create_sku_task("expired")
        self.assertEqual((await self.db.get_queue_task(task_id)).account_id, "account-a")

    async def test_retry_limit_errors_restart_and_cancel(self):
        task_id = await self.db.create_sku_task("retry", account_id="account-a")
        await self.db.claim_next_task()
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="FIRST"), "queued")
        task = await self.db.get_queue_task(task_id)
        self.assertEqual((task.account_id, task.payload["attempt"]), ("account-b", 2))
        await self.db.initialize()
        await self.db.claim_next_task()
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="SECOND"), "failed")
        attempts = await self.db.list_task_attempts(task_id)
        self.assertEqual([a["error_code"] for a in attempts], ["FIRST", "SECOND"])
        for running in (False, True):
            cancelled = await self.db.create_sku_task("cancel", account_id="account-a")
            if running:
                await self.db.claim_next_task()
            await self.db.cancel_queue_task(cancelled)
            if running:
                await self.db.finish_queue_task(cancelled, status="failed", error_code="late")
            self.assertEqual((await self.db.get_queue_task(cancelled)).status, "cancelled")
            self.assertLessEqual(len(await self.db.list_task_attempts(cancelled)), 1)
        await self.db.pause_account("account-b", "risk")
        single = await self.db.create_sku_task("single", account_id="account-a")
        await self.db.claim_next_task()
        self.assertEqual(await self.db.finish_queue_task(single, status="failed", error_code="original"), "failed")
        self.assertEqual((await self.db.get_queue_task(single)).error_code, "original")


if __name__ == "__main__":
    unittest.main()
