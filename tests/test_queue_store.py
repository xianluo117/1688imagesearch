"""Offline SQLite contracts; no workers, HTTP clients or upstream calls."""
from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from api.database import Database
from api.queue_store import IdempotencyConflict, QueueNotAccepting
from product_sku.models import Sku, SkuResult


class QueueStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Database(Path(self.temp.name) / "queue.db")
        await self.store.initialize()
        await self.store.store_cookie(b"offline", cookie_count=1, exported_at=None)

    async def ready_upload(self):
        task_id = await self.store.create_upload_task("image")
        await self.store.claim_upload_task()
        await self.store.complete_upload_task(task_id, image_id="image-id", search_page_url="search")
        return task_id

    async def scalar(self, sql, args=()):
        async with aiosqlite.connect(self.store.path) as db:
            c = await db.execute(sql, args)
            return (await c.fetchone())[0]

    async def test_mixed_fifo_and_global_default_execution_limit(self):
        parent = await self.ready_upload()
        with patch("api.queue_store.time.time", return_value=123):
            upload = await self.store.create_upload_task("next")
            sku = await self.store.create_sku_task("sku")
            product, error = await self.store.create_product_task(parent)
        self.assertIsNone(error)
        seqs = []
        for task_id in (upload, sku, product):
            task = await self.store.claim_next_task()
            self.assertEqual(task.task_id, task_id)
            seqs.append(task.seq)
            self.assertIsNone(await self.store.claim_next_task())
            await self.store.finish_queue_task(task_id, status="failed", error_code="OFFLINE")
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertEqual((await self.store.get_product_task(product)).status, "failed")

    async def test_atomic_mixed_capacity_across_connections(self):
        parent = await self.ready_upload()
        self.store.queue_capacity = 7
        other = Database(self.store.path, queue_capacity=7)
        jobs = []
        for i in range(12):
            jobs.extend((self.store.create_upload_task(str(i)), other.create_sku_task(str(i)),
                         self.store.create_product_task(parent)))
        results = await asyncio.gather(*jobs)
        ids = [r[0] if isinstance(r, tuple) else r for r in results]
        self.assertEqual(len([r for r in ids if r]), 7)
        self.assertEqual((await self.store.queue_task_counts())["queued"], 7)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM upload_tasks WHERE status='queued'"),
                         await self.scalar("SELECT COUNT(*) FROM queue_tasks WHERE kind='upload' AND status='queued'"))

    async def test_default_capacity_is_100_and_running_counts(self):
        for i in range(100):
            self.assertIsNotNone(await self.store.create_sku_task(str(i)))
        task = await self.store.claim_next_task()
        self.assertIsNone(await self.store.create_upload_task("full"))
        await self.store.cancel_queue_task(task.task_id)
        self.assertIsNone(await self.store.create_sku_task("still-full"))
        await self.store.finish_queue_task(task.task_id, status="failed")
        self.assertIsNotNone(await self.store.create_upload_task("space"))

    async def test_parallel_claims_enforce_global_limit(self):
        for i in range(12):
            await self.store.create_sku_task(str(i))
        claims = await asyncio.gather(*(self.store.claim_next_task(max_running=3) for _ in range(12)))
        ids = [task.task_id for task in claims if task]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual((await self.store.queue_task_counts())["running"], 3)

    async def test_parallel_idempotency_and_conflicts(self):
        ids = await asyncio.gather(*(self.store.create_sku_task("url", idempotency_key="key") for _ in range(15)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual((await self.store.queue_task_counts())["queued"], 1)
        with self.assertRaises(IdempotencyConflict):
            await self.store.create_sku_task("different", idempotency_key="key")
        with self.assertRaises(IdempotencyConflict):
            await self.store.create_upload_task("url", idempotency_key="key")
        await self.store.cancel_queue_task(ids[0])
        await self.store.initialize()
        self.assertEqual(await self.store.create_sku_task("url", idempotency_key="key"), ids[0])
        self.assertNotEqual(await self.store.create_sku_task("url"), await self.store.create_sku_task("url"))

    async def test_business_idempotency_and_full_queue_reuse(self):
        self.store.queue_capacity = 1
        upload = await self.store.create_upload_task("image", idempotency_key="upload")
        self.assertEqual(await self.store.create_upload_task("image", idempotency_key="upload"), upload)
        await self.store.claim_next_task()
        await self.store.complete_upload_task(upload, image_id="id", search_page_url="url")
        product, error = await self.store.create_product_task(upload, idempotency_key="product")
        self.assertIsNone(error)
        self.assertEqual(await self.store.create_product_task(upload, idempotency_key="product"), (product, None))
        with self.assertRaises(IdempotencyConflict):
            await self.store.create_product_task("missing", idempotency_key="product")

    async def test_product_validation_is_compatible(self):
        self.assertEqual(await self.store.create_product_task("missing"), (None, "UPLOAD_TASK_NOT_FOUND"))
        upload = await self.store.create_upload_task("image")
        self.assertEqual(await self.store.create_product_task(upload), (None, "UPLOAD_TASK_NOT_READY"))
        await self.store.cancel_upload_task(upload)
        self.assertEqual(await self.store.create_product_task(upload), (None, "UPLOAD_TASK_CANCELLED"))
        upload = await self.store.create_upload_task("image")
        await self.store.claim_next_task()
        await self.store.fail_upload_task(upload, "FAIL", "failed")
        self.assertEqual(await self.store.create_product_task(upload), (None, "UPLOAD_TASK_FAILED"))

    async def test_business_state_updates_mirror_and_do_not_overwrite_terminal(self):
        upload = await self.ready_upload()
        before = await self.store.get_queue_task(upload)
        await self.store.fail_upload_task(upload, "LATE", "late")
        await self.store.complete_upload_task(upload, image_id="late", search_page_url="late")
        await self.store.cancel_upload_task(upload)
        self.assertEqual(await self.store.get_queue_task(upload), before)
        product, _ = await self.store.create_product_task(upload)
        await self.store.claim_product_task()
        await self.store.cancel_product_task(product)
        self.assertEqual((await self.store.get_queue_task(product)).status, "running")
        self.assertEqual(await self.store.complete_product_task(product, {"products": [1]}), "cancelled")
        terminal = await self.store.get_queue_task(product)
        self.assertIsNone(terminal.result)
        await self.store.fail_product_task(product, "LATE", "late")
        self.assertEqual(await self.store.get_queue_task(product), terminal)

    async def test_running_cancel_holds_slot_until_actual_completion(self):
        task_id = await self.store.create_sku_task("first")
        await self.store.create_upload_task("second")
        await self.store.claim_next_task()
        cancelled = await self.store.cancel_queue_task(task_id)
        self.assertTrue(cancelled.cancel_requested)
        self.assertEqual(cancelled.status, "running")
        self.assertIsNone(cancelled.finished_at)
        self.assertIsNone(await self.store.claim_next_task())
        self.assertEqual(await self.store.complete_sku_task(task_id, {"status": "success", "skus": [{}]}), "cancelled")
        final = await self.store.get_queue_task(task_id)
        await self.store.finish_queue_task(task_id, status="failed", error_code="LATE")
        self.assertEqual(await self.store.get_queue_task(task_id), final)
        self.assertIsNotNone(await self.store.claim_next_task())

    async def test_queued_cancel_is_immediate_and_unknown_operations(self):
        task_id = await self.store.create_sku_task("url")
        task = await self.store.cancel_queue_task(task_id)
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(await self.store.claim_next_task())
        self.assertEqual(await self.store.complete_sku_task(task_id, {"status": "success", "skus": [1]}), "cancelled")
        self.assertIsNone(await self.store.cancel_queue_task("missing"))
        self.assertIsNone(await self.store.finish_queue_task("missing", status="failed"))
        self.assertTrue(await self.store.is_queue_cancel_requested("missing"))

    async def test_sku_original_result_roundtrip_and_business_status_mapping(self):
        for business_status, skus, expected in (
            ("success", [Sku("1", [], price="12.3456")], "succeeded"),
            ("partial_success", [Sku("2", [])], "succeeded"),
            ("success", [], "failed"),
            ("access_restricted", [], "failed"),
            ("login_required", [], "failed"),
            ("parse_failed", [], "failed"),
        ):
            with self.subTest(status=business_status, expected=expected):
                task_id = await self.store.create_sku_task("url")
                await self.store.claim_next_task()
                raw = SkuResult("id", "url", business_status, "原始原因", skus=skus).to_dict()
                self.assertEqual(await self.store.complete_sku_task(task_id, raw), expected)
                self.assertEqual((await self.store.get_queue_task(task_id)).result, raw)
        await self.store.initialize()
        self.assertEqual((await self.store.get_queue_task(task_id)).result, raw)

    async def test_protection_priority_persistence_expiry_and_manual_resume(self):
        now = time.time()
        state = await self.store.set_cooldown("rate", until=now + 100)
        self.assertEqual(state.until, now + 100)
        self.assertEqual((await self.store.set_cooldown("shorter", until=now + 10)).reason, "rate")
        self.assertEqual((await self.store.set_cooldown("longer", until=now + 200)).until, now + 200)
        await self.store.create_sku_task("queued")
        self.assertIsNone(await self.store.claim_next_task())
        await self.store.initialize()
        self.assertEqual((await self.store.get_scheduler_state()).state, "cooldown")
        self.assertEqual((await self.store.get_scheduler_state(now=now + 201)).state, "normal")
        await self.store.pause_scheduler("login_required")
        await self.store.set_cooldown("rate", until=now + 500)
        await self.store.store_cookie(b"new", cookie_count=1, exported_at=None)
        await self.store.initialize()
        state = await self.store.get_scheduler_state(now=now + 1000)
        self.assertEqual((state.state, state.reason, state.until), ("paused", "login_required", None))
        self.assertIsNone(await self.store.claim_next_task())
        state = await self.store.resume_scheduler()
        self.assertEqual((state.state, state.reason, state.until), ("normal", None, None))
        self.assertIsNotNone(await self.store.claim_next_task())

    async def test_shutdown_rejects_all_admission_and_claims_but_allows_drain(self):
        parent = await self.ready_upload()
        sku = await self.store.create_sku_task("sku", idempotency_key="key")
        await self.store.claim_next_task()
        self.store.accepting = False
        for job in (self.store.create_upload_task("image"), self.store.create_product_task(parent),
                    self.store.create_sku_task("new"), self.store.create_sku_task("sku", idempotency_key="key")):
            with self.assertRaises(QueueNotAccepting):
                await job
        self.assertIsNone(await self.store.claim_next_task(max_running=4))
        self.assertIsNone(await self.store.claim_upload_task())
        self.assertEqual(await self.store.finish_queue_task(sku, status="failed"), "failed")
        self.assertFalse((await self.store.queue_snapshot())["accepting"])

    async def test_restart_requeues_running_but_never_requested_cancellations(self):
        ids = [await self.store.create_upload_task("upload"), await self.store.create_sku_task("sku"),
               await self.store.create_sku_task("cancel-running"), await self.store.create_sku_task("cancel-queued")]
        for _ in range(3):
            await self.store.claim_next_task(max_running=3)
        await self.store.cancel_queue_task(ids[2])
        # Simulate old queued records with a cancellation request not yet finalized.
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute("UPDATE queue_tasks SET cancel_requested=1 WHERE task_id=?", (ids[3],))
            await db.commit()
        seqs = [(await self.store.get_queue_task(i)).seq for i in ids]
        await self.store.initialize()
        await self.store.initialize()
        tasks = [await self.store.get_queue_task(i) for i in ids]
        self.assertEqual([t.seq for t in tasks], seqs)
        self.assertEqual([t.status for t in tasks], ["queued", "queued", "cancelled", "cancelled"])
        self.assertIsNone(tasks[0].started_at)
        self.assertEqual((await self.store.get_upload_task(ids[0])).status, "queued")

    async def test_cleanup_retains_dependencies_and_removes_results_keys_once(self):
        parent = await self.ready_upload()
        product, _ = await self.store.create_product_task(parent, idempotency_key="product")
        sku = await self.store.create_sku_task("sku", idempotency_key="sku")
        await self.store.cancel_queue_task(sku)
        last_seq = (await self.store.get_queue_task(sku)).seq
        cutoff = time.time() + 10
        self.assertEqual(await self.store.cleanup_tasks(cutoff), 1)
        self.assertIsNotNone(await self.store.get_queue_task(parent))
        self.assertIsNotNone(await self.store.get_upload_task(parent))
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_idempotency"), 1)
        await self.store.claim_next_task()
        self.assertEqual(await self.store.cleanup_tasks(cutoff), 0)
        await self.store.complete_product_task(product, {"products": [{"id": "1"}]})
        self.assertEqual(await self.store.cleanup_tasks(cutoff), 2)
        self.assertEqual(await self.store.cleanup_tasks(cutoff), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_idempotency"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM product_tasks"), 0)
        await self.store.initialize()
        new = await self.store.create_sku_task("different", idempotency_key="sku")
        self.assertGreater((await self.store.get_queue_task(new)).seq, last_seq)

    async def test_recent_terminal_child_keeps_old_parent(self):
        parent = await self.ready_upload()
        cutoff = time.time()
        product, _ = await self.store.create_product_task(parent)
        await self.store.cancel_queue_task(product)
        self.assertEqual(await self.store.cleanup_tasks(cutoff), 0)
        self.assertIsNotNone(await self.store.get_upload_task(parent))

    async def test_transaction_rolls_back_business_and_queue_on_insert_failure(self):
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute("""CREATE TRIGGER reject_upload BEFORE INSERT ON upload_tasks
                BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
            await db.commit()
        with self.assertRaises(aiosqlite.IntegrityError):
            await self.store.create_upload_task("image", idempotency_key="key")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_tasks"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_idempotency"), 0)

    async def test_unified_finish_updates_business_results_and_failures(self):
        upload = await self.store.create_upload_task("image")
        await self.store.claim_next_task()
        await self.store.finish_queue_task(upload, status="succeeded", result={"image_id": "id", "search_page_url": "url"})
        self.assertEqual((await self.store.get_upload_task(upload)).image_id, "id")
        product, _ = await self.store.create_product_task(upload)
        await self.store.claim_next_task()
        await self.store.finish_queue_task(product, status="succeeded", result={"products": [1]})
        self.assertEqual((await self.store.get_product_task(product)).result, {"products": [1]})
        sku = await self.store.create_sku_task("sku")
        await self.store.claim_next_task()
        await self.store.finish_queue_task(sku, status="failed", error_code="NETWORK", error_message="offline")
        self.assertEqual((await self.store.get_queue_task(sku)).error_code, "NETWORK")

    async def test_cancel_completion_race_has_one_immutable_outcome(self):
        for i in range(8):
            task_id = await self.store.create_sku_task(str(i))
            await self.store.claim_next_task()
            await asyncio.gather(
                self.store.cancel_queue_task(task_id),
                self.store.complete_sku_task(task_id, {"status": "success", "skus": [{"sku_id": "1"}]}),
            )
            terminal = await self.store.get_queue_task(task_id)
            self.assertIn(terminal.status, ("cancelled", "succeeded"))
            self.assertEqual((await self.store.queue_task_counts())["running"], 0)
            await self.store.finish_queue_task(task_id, status="failed", error_code="late")
            self.assertEqual(await self.store.get_queue_task(task_id), terminal)

    async def test_sql_triggers_prevent_terminal_business_and_queue_overwrite(self):
        upload = await self.ready_upload()
        original = await self.store.get_queue_task(upload)
        async with aiosqlite.connect(self.store.path) as db:
            await db.execute("UPDATE upload_tasks SET status='failed',image_id='late' WHERE task_id=?", (upload,))
            await db.execute("UPDATE queue_tasks SET status='queued',result_json=NULL WHERE task_id=?", (upload,))
            await db.commit()
        self.assertEqual(await self.store.get_queue_task(upload), original)
        self.assertEqual((await self.store.get_upload_task(upload)).image_id, "image-id")

    async def test_shutdown_during_insert_rolls_back_every_record(self):
        original = aiosqlite.Connection.execute
        store = self.store

        async def execute(connection, sql, parameters=None):
            result = await original(connection, sql, parameters)
            if "INSERT INTO upload_tasks" in sql:
                store.accepting = False
            return result

        with patch.object(aiosqlite.Connection, "execute", execute):
            with self.assertRaises(QueueNotAccepting):
                await store.create_upload_task("image", idempotency_key="stopping")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_tasks"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM upload_tasks"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM queue_idempotency"), 0)

    async def test_legacy_max_active_limits_the_shared_count(self):
        await self.store.create_sku_task("sku")
        self.assertIsNone(await self.store.create_upload_task("image", max_active=1))
        self.assertIsNotNone(await self.store.create_upload_task("image", max_active=2))

    async def test_snapshot_and_parameter_validation(self):
        await self.store.create_sku_task("sku")
        await self.store.pause_scheduler("verify")
        snapshot = await self.store.queue_snapshot()
        self.assertEqual(snapshot["counts"]["queued"], 1)
        self.assertEqual(snapshot["capacity"], 100)
        self.assertEqual(snapshot["scheduler"]["reason"], "verify")
        with self.assertRaises(ValueError):
            await self.store.claim_next_task(max_running=0)
        with self.assertRaises(ValueError):
            await self.store.create_sku_task("sku", max_active=0)
        with self.assertRaises(ValueError):
            Database(self.store.path, queue_capacity=0)


if __name__ == "__main__":
    unittest.main()
