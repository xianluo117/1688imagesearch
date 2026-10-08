"""Build a pre-unified database directly, then exercise real startup migration."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import aiosqlite

from api.database import Database


class QueueMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Database(Path(self.temp.name) / "legacy.db")
        async with aiosqlite.connect(self.store.path) as db:
            await db.executescript("""
                CREATE TABLE cookie_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT, encrypted_payload BLOB NOT NULL,
                    cookie_count INTEGER NOT NULL, exported_at TEXT, uploaded_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1);
                INSERT INTO cookie_versions VALUES (1, X'00', 1, NULL, 1, 1);
                CREATE TABLE upload_tasks (
                    task_id TEXT PRIMARY KEY, status TEXT NOT NULL, image_url TEXT NOT NULL,
                    cookie_version INTEGER, image_id TEXT, search_page_url TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT, error_message TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL);
                CREATE TABLE product_tasks (
                    task_id TEXT PRIMARY KEY, upload_task_id TEXT NOT NULL, status TEXT NOT NULL,
                    cookie_version INTEGER NOT NULL, result_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0, error_code TEXT, error_message TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL);
                INSERT INTO upload_tasks VALUES
                    ('parent','succeeded','image',1,'image-id','search',0,NULL,NULL,1,2,1,2),
                    ('running','running','image',1,NULL,NULL,0,NULL,NULL,3,4,4,NULL),
                    ('cancel-running','running','image',1,NULL,NULL,1,NULL,NULL,4,5,5,NULL),
                    ('cancel-queued','queued','image',NULL,NULL,NULL,1,NULL,NULL,5,5,NULL,NULL),
                    ('waiting-parent','queued','image',NULL,NULL,NULL,0,NULL,NULL,30,30,NULL,NULL);
                INSERT INTO product_tasks VALUES
                    ('done-product','parent','succeeded',1,'{"products":[{"id":"kept"}]}',0,NULL,NULL,2,3,2,3),
                    ('running-product','parent','running',1,NULL,0,NULL,NULL,6,7,7,NULL),
                    ('cancel-product','parent','running',1,NULL,1,NULL,NULL,7,8,8,NULL),
                    ('blocked-product','waiting-parent','queued',1,NULL,0,NULL,NULL,8,8,NULL,NULL);
            """)
            await db.commit()

    async def test_migration_preserves_ids_results_dependencies_and_is_idempotent(self):
        await self.store.initialize()
        ids = ("parent", "done-product", "running", "cancel-running", "cancel-queued",
               "running-product", "cancel-product", "blocked-product", "waiting-parent")
        first = [await self.store.get_queue_task(task_id) for task_id in ids]
        self.assertEqual([t.seq for t in first], list(range(1, 10)))
        self.assertEqual([t.status for t in first], ["succeeded", "succeeded", "queued", "cancelled",
                         "cancelled", "queued", "cancelled", "queued", "queued"])
        self.assertEqual(first[1].result, {"products": [{"id": "kept"}]})
        self.assertEqual(first[1].dependency_id, "parent")
        self.assertEqual(first[0].result, {"image_id": "image-id", "search_page_url": "search"})
        self.assertEqual((await self.store.get_product_task("done-product")).result, first[1].result)
        await self.store.initialize()
        second = [await self.store.get_queue_task(task_id) for task_id in ids]
        self.assertEqual(first, second)
        added = await self.store.create_sku_task("sku")
        self.assertEqual((await self.store.get_queue_task(added)).seq, 10)
        async with aiosqlite.connect(self.store.path) as db:
            c = await db.execute("PRAGMA foreign_key_check")
            self.assertEqual(await c.fetchall(), [])

    async def test_claim_skips_unready_dependency_and_retains_oldest_ready_order(self):
        await self.store.initialize()
        expected = ["running", "running-product", "waiting-parent"]
        for task_id in expected:
            task = await self.store.claim_next_task()
            self.assertEqual(task.task_id, task_id)
            await self.store.finish_queue_task(task_id, status="succeeded", result=(
                {"image_id": "id", "search_page_url": "url"} if task.kind == "upload" else {"products": []}))
        self.assertEqual((await self.store.claim_next_task()).task_id, "blocked-product")

    async def test_legacy_cancelled_and_recovered_business_rows_match_queue(self):
        await self.store.initialize()
        for task_id in ("running", "cancel-running", "cancel-queued", "waiting-parent"):
            business = await self.store.get_upload_task(task_id)
            task = await self.store.get_queue_task(task_id)
            self.assertEqual((business.status, business.cancel_requested, business.started_at, business.finished_at),
                             (task.status, task.cancel_requested, task.started_at, task.finished_at))
        for task_id in ("running-product", "cancel-product"):
            business = await self.store.get_product_task(task_id)
            self.assertEqual(business.status, (await self.store.get_queue_task(task_id)).status)


if __name__ == "__main__":
    unittest.main()
