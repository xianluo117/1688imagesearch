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
        self.assertEqual(task.account_id, "")
        self.assertEqual(task.payload["requested_account_id"], "account-a")
        self.assertIsNone(task.payload["cookie_version"])
        await self.db.store_cookie(b"latest", cookie_count=1, exported_at=None, account_id="account-a")
        task = await self.db.claim_next_task()
        self.assertEqual(task.account_id, "account-a")
        self.assertEqual(task.payload["cookie_version"], (await self.db.get_active_cookie("account-a"))[0])


    async def test_concurrent_mixed_rotation_and_restart(self):
        await self.db.store_cookie(b"c", cookie_count=1, exported_at=None, account_id="account-c")
        ids = await asyncio.gather(*(
            self.db.create_sku_task(str(i)) if i % 2 else self.db.create_upload_task(str(i))
            for i in range(18)
        ))
        tasks = sorted(await asyncio.gather(*(self.db.get_queue_task(i) for i in ids)), key=lambda t: t.seq)
        self.assertEqual([t.account_id for t in tasks if t.kind == "upload"], ["account-a", "account-b", "account-c"] * 3)
        self.assertTrue(all(t.account_id == "" for t in tasks if t.kind == "sku"))
        await self.db.initialize()
        next_id = await self.db.create_sku_task("next")
        self.assertEqual((await self.db.get_queue_task(next_id)).account_id, "")
        claimed = [await self.db.claim_next_task(max_running=19) for _ in range(19)]
        self.assertEqual([t.task_id for t in claimed], [t.task_id for t in tasks] + [next_id])
        self.assertLessEqual(max(Counter(t.account_id for t in claimed).values()), 7)

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
        self.assertEqual((await self.db.get_queue_task(next_id)).account_id, "")
        self.assertEqual((await self.db.claim_next_task()).account_id, "account-a")

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
        task_id = await self.db.create_sku_task("none")
        self.assertIsNotNone(task_id)
        self.assertIsNone(await self.db.claim_next_task())
        await self.db.initialize()
        self.assertEqual((await self.db.get_account_state("account-b"))["state"], "paused")
        with patch("api.account_store.time.time", return_value=time.time() + 120):
            claimed = await self.db.claim_next_task()
        self.assertEqual(claimed.task_id, task_id)
        self.assertEqual(claimed.account_id, "account-a")

    async def test_retry_limit_errors_restart_and_cancel(self):
        task_id = await self.db.create_sku_task("retry")
        await self.db.claim_next_task()
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="FIRST"), "queued")
        task = await self.db.get_queue_task(task_id)
        self.assertEqual((task.account_id, task.payload["attempt"]), ("", 2))
        self.assertEqual(task.payload["retry_exclude_account_id"], "account-a")
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


    async def test_public_oldest_available_load_rotation_and_atomic_limits(self):
        busy = await self.db.create_sku_task("busy", account_id="account-a")
        await self.db.claim_next_task(max_running=4, max_account_running=2)
        ids = [await self.db.create_sku_task(str(i)) for i in range(4)]
        first = await self.db.claim_next_task(max_running=4, max_account_running=2)
        self.assertEqual((first.task_id, first.account_id), (ids[0], "account-b"))
        claims = await asyncio.gather(*(self.db.claim_next_task(max_running=4, max_account_running=2) for _ in range(8)))
        claimed = sorted((t for t in claims if t), key=lambda t: t.seq)
        self.assertEqual([t.task_id for t in claimed], ids[1:3])
        self.assertEqual([t.account_id for t in claimed], ["account-a", "account-b"])
        self.assertEqual((await self.db.queue_task_counts())["running"], 4)
        await self.db.finish_queue_task(busy, status="cancelled")
        self.assertEqual((await self.db.claim_next_task(max_running=4, max_account_running=2)).task_id, ids[3])

    async def test_no_accounts_admission_capacity_restart_and_new_cookie(self):
        await self.db.delete_account("account-a")
        await self.db.delete_account("account-b")
        task_id = await self.db.create_sku_task("waiting", idempotency_key="waiting")
        self.assertIsNotNone(task_id)
        self.assertIsNone(await self.db.create_sku_task("full", max_active=1))
        self.assertIsNone(await self.db.claim_next_task())
        await self.db.initialize()
        task = await self.db.get_queue_task(task_id)
        self.assertEqual(task.account_id, "")
        self.assertIsNone(task.payload["cookie_version"])
        await self.db.store_cookie(b"new", cookie_count=1, exported_at=None, account_id="new-account")
        self.assertEqual(await self.db.create_sku_task("waiting", idempotency_key="waiting"), task_id)
        claimed = await self.db.claim_next_task()
        self.assertEqual((claimed.task_id, claimed.account_id), (task_id, "new-account"))

    async def test_explicit_paused_delete_and_no_cross_account_retry(self):
        await self.db.pause_account("account-a", "paused")
        explicit = await self.db.create_sku_task("explicit", account_id="account-a")
        public = await self.db.create_sku_task("public")
        claimed = await self.db.claim_next_task()
        self.assertEqual((claimed.task_id, claimed.account_id), (public, "account-b"))
        await self.db.delete_account("account-a")
        task = await self.db.get_queue_task(explicit)
        self.assertEqual((task.status, task.error_code, task.account_id), ("failed", "ACCOUNT_UNAVAILABLE", ""))
        self.assertEqual(await self.db.list_task_attempts(explicit), [])
        self.assertEqual(await self.db._enqueue("sku", {"product_url": "missing"}, max_active=None,
                         idempotency_key=None, account_id="missing"), (None, "ACCOUNT_UNAVAILABLE"))
        await self.db.finish_queue_task(public, status="cancelled")
        await self.db.store_cookie(b"a", cookie_count=1, exported_at=None, account_id="account-a")
        explicit = await self.db.create_sku_task("explicit-fail", account_id="account-a")
        await self.db.claim_next_task()
        self.assertEqual(await self.db.finish_queue_task(explicit, status="failed", error_code="RISK_CONTROL"), "failed")
        self.assertEqual(len(await self.db.list_task_attempts(explicit)), 1)

    async def test_retry_stays_in_place_waits_and_excludes_first_account(self):
        task_id = await self.db.create_sku_task("retry")
        first = await self.db.claim_next_task()
        later = await self.db.create_sku_task("later", account_id="account-a")
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="RISK_CONTROL"), "queued")
        retry = await self.db.get_queue_task(task_id)
        self.assertEqual((retry.seq, retry.created_at, retry.account_id), (first.seq, first.created_at, ""))
        await self.db.pause_account("account-b", "pause")
        await self.db.recover_account_deletions()
        self.assertEqual((await self.db.get_queue_task(task_id)).account_id, "")
        self.assertEqual((await self.db.claim_next_task()).task_id, later)
        await self.db.finish_queue_task(later, status="cancelled")
        self.assertIsNone(await self.db.claim_next_task())
        await self.db.store_cookie(b"latest", cookie_count=1, exported_at=None, account_id="account-b", confirmed_login=True)
        retry = await self.db.claim_next_task()
        self.assertEqual((retry.task_id, retry.account_id), (task_id, "account-b"))
        self.assertEqual(retry.payload["cookie_version"], (await self.db.get_active_cookie("account-b"))[0])
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="SECOND"), "failed")

    async def test_legacy_binding_retry_and_running_restart_unchanged(self):
        task_id = await self.db.create_sku_task("legacy", account_id="account-a")
        version = (await self.db.get_active_cookie("account-a"))[0]
        async with self.db._queue_connection() as db:
            await db.execute("""UPDATE queue_tasks SET account_id='account-a',
                payload_json=json_object('product_url','legacy','cookie_version',?) WHERE task_id=?""", (version, task_id))
            await db.commit()
        await self.db.store_cookie(b"new", cookie_count=1, exported_at=None, account_id="account-a")
        await self.db.initialize()
        task = await self.db.claim_next_task()
        self.assertEqual((task.account_id, task.payload["cookie_version"]), ("account-a", version))
        self.assertEqual(await self.db.finish_queue_task(task_id, status="failed", error_code="FIRST"), "queued")
        self.assertEqual((await self.db.get_queue_task(task_id)).account_id, "account-b")
        await self.db.cancel_queue_task(task_id)
        new = await self.db.create_sku_task("restart")
        claimed = await self.db.claim_next_task()
        await self.db.initialize()
        recovered = await self.db.claim_next_task()
        self.assertEqual((recovered.task_id, recovered.account_id, recovered.payload),
                         (new, claimed.account_id, claimed.payload))

if __name__ == "__main__":
    unittest.main()
