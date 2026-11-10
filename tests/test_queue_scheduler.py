"""Offline scheduler lifecycle tests with real SQLite and blocked fake transports."""
import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.config import Settings
from api.database import Database
from api.queue_scheduler import UnifiedQueueScheduler
from api.worker import QueueTaskExecutor, UploadWorkerPool, ProductWorkerPool
from product_sku.models import Sku, SkuResult


RAW = SkuResult("1", "https://detail.1688.com/offer/1.html", "success", "", skus=[Sku("s", [])]).to_dict()


def make_settings(path, **kwargs):
    return Settings(cookie_upload_api_key="u" * 24, search_api_key="s" * 24,
                    cookie_encryption_key="offline", database_path=path,
                    request_interval_min_seconds=0.02, request_interval_max_seconds=0.02,
                    account_request_interval_min_seconds=0.02, account_request_interval_max_seconds=0.02,
                    **kwargs)


async def eventually(predicate, timeout=3):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "queue.db")
        await self.db.initialize()
        await self.db.store_cookie(b"offline", cookie_count=1, exported_at=None)
        self.cookies = SimpleNamespace(load_active=AsyncMock(return_value=(1, [])),
                                       load_version=AsyncMock(return_value=(1, [])))
        self.settings = make_settings(self.db.path)
        self.schedulers = []
        self.releases = []

    async def asyncTearDown(self):
        for event in self.releases:
            event.set()
        for scheduler in self.schedulers:
            await asyncio.wait_for(scheduler.stop(), 5)

    def scheduler(self, callback=None, **kwargs):
        scheduler = UnifiedQueueScheduler(self.db, self.cookies, kwargs.pop("settings", self.settings),
                                          execute_sku_sync=callback, poll_seconds=0.005, **kwargs)
        self.schedulers.append(scheduler)
        return scheduler

    def event(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    async def test_single_pool_mixed_fifo_default_one(self):
        parent = await self.db.create_upload_task("parent")
        await self.db.claim_next_task()
        await self.db.finish_queue_task(parent, status="succeeded", result={"image_id": "id", "search_page_url": "url"})
        upload = await self.db.create_upload_task("upload")
        sku = await self.db.create_sku_task("sku")
        product, _ = await self.db.create_product_task(parent)
        order, threads = [], []

        class Executor:
            sku_sync = None

            async def execute(inner, task, control, run_sync, hook):
                def sync():
                    control.before_request()
                    order.append(task.task_id)
                    threads.append(threading.current_thread().name)
                    return RAW if task.kind == "sku" else {"image_id": "id", "search_page_url": "url"}
                return await run_sync(sync)

        scheduler = self.scheduler(task_executor=Executor())
        await scheduler.start()
        await scheduler.wait_for_task(product, timeout=3)
        self.assertEqual(order, [upload, sku, product])
        self.assertEqual(len(set(threads)), 1)
        self.assertTrue(threads[0].startswith("1688-unified"))
        self.assertEqual((await self.db.queue_task_counts())["running"], 0)

    async def test_higher_concurrency_still_shares_every_request_interval(self):
        starts = []
        lock = threading.Lock()

        def fetch(url, records, control):
            for _ in range(2):
                control.before_request()
                with lock:
                    starts.append(time.monotonic())
            return RAW

        ids = [await self.db.create_sku_task(str(i)) for i in range(3)]
        scheduler = self.scheduler(fetch, settings=replace(self.settings, global_worker_count=3))
        await scheduler.start()
        await asyncio.gather(*(scheduler.wait_for_task(i, timeout=4) for i in ids))
        self.assertEqual(len(starts), 6)
        self.assertTrue(all(b - a >= 0.017 for a, b in zip(starts, starts[1:])))

    async def test_cancel_and_timeout_hold_slot_until_network_returns_and_block_new_request(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                self.db.accepting = True
                entered, release = threading.Event(), self.event()
                later_requests = []

                def fetch(url, records, control):
                    control.before_request()
                    entered.set()
                    release.wait(5)
                    control.before_request()
                    later_requests.append(url)
                    return RAW

                first = await self.db.create_sku_task("blocked")
                second = await self.db.create_sku_task("queued")
                scheduler = self.scheduler(fetch, settings=replace(self.settings, sku_task_timeout_seconds=0.25))
                await scheduler.start()
                await eventually(entered.is_set)
                if cancel:
                    await self.db.cancel_queue_task(first)
                else:
                    await asyncio.sleep(0.35)
                self.assertEqual((await self.db.get_queue_task(first)).status, "running")
                self.assertEqual((await self.db.get_queue_task(second)).status, "queued")
                await self.db.cancel_queue_task(second)
                release.set()
                result = await scheduler.wait_for_task(first, timeout=3)
                self.assertEqual(result.error_code, "TASK_CANCELLED" if cancel else "TASK_TIMEOUT")
                self.assertIsNone(result.result)
                self.assertFalse(later_requests)
                await scheduler.stop()

    async def test_late_success_does_not_override_timeout(self):
        entered, release = threading.Event(), self.event()

        def fetch(url, records, control):
            control.before_request()
            entered.set()
            release.wait(5)
            return RAW

        task_id = await self.db.create_sku_task("blocked")
        scheduler = self.scheduler(fetch, settings=replace(self.settings, sku_task_timeout_seconds=10))
        offset = 0
        scheduler.guard.clock = lambda: time.monotonic() + offset
        await scheduler.start()
        await eventually(entered.is_set)
        offset = 11
        release.set()
        task = await scheduler.wait_for_task(task_id, timeout=3)
        self.assertEqual(task.error_code, "TASK_TIMEOUT")
        self.assertIsNone(task.result)

    async def test_shutdown_after_account_pause_keeps_bound_tasks_queued(self):
        waiting = threading.Event()

        def fetch(url, records, control):
            control.before_request()
            control.observe_result({"status": "login_required", "reason": ""})
            waiting.set()
            control.before_request()
            self.fail("shutdown must not grant a new request")

        first = await self.db.create_sku_task("first")
        second = await self.db.create_sku_task("second")
        scheduler = self.scheduler(fetch)
        await scheduler.start()
        await eventually(waiting.is_set)
        result = await scheduler.wait_for_task(first, timeout=2)
        self.assertEqual(result.status, "failed")
        await asyncio.wait_for(scheduler.stop(), 2)
        self.assertEqual((await self.db.get_queue_task(second)).status, "queued")
        self.assertEqual((await self.db.get_account_state("default"))["state"], "paused")
        self.assertEqual((await self.db.get_scheduler_state()).state, "normal")
        self.assertFalse(self.db.accepting)

    async def test_shutdown_waits_for_actual_network_and_survives_cancelled_stop_waiter(self):
        entered, release = threading.Event(), self.event()

        def fetch(url, records, control):
            control.before_request()
            entered.set()
            release.wait(5)
            return RAW

        first = await self.db.create_sku_task("first")
        second = await self.db.create_sku_task("second")
        scheduler = self.scheduler(fetch)
        await scheduler.start()
        await eventually(entered.is_set)
        stop = asyncio.create_task(scheduler.stop())
        await asyncio.sleep(0.1)
        self.assertFalse(stop.done())
        self.assertEqual((await self.db.get_queue_task(first)).status, "running")
        stop.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stop
        release.set()
        await asyncio.wait_for(scheduler.stop(), 3)
        self.assertEqual((await self.db.get_queue_task(second)).status, "queued")
        self.assertEqual((await self.db.get_queue_task(first)).error_code, "SCHEDULER_STOPPING")

    async def test_wait_timeout_and_disconnect_do_not_cancel_task(self):
        entered, release = threading.Event(), self.event()

        def fetch(url, records, control):
            control.before_request()
            entered.set()
            release.wait(5)
            return SkuResult("1", "url", "partial_success", "", skus=[Sku("s", [])])

        first = await self.db.create_sku_task("first")
        scheduler = self.scheduler(fetch)
        await scheduler.start()
        await eventually(entered.is_set)
        with self.assertRaises(TimeoutError):
            await scheduler.wait_for_task(first, timeout=0.03)
        waiter = asyncio.create_task(scheduler.wait_for_task(first))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse((await self.db.get_queue_task(first)).cancel_requested)
        release.set()
        result = await scheduler.wait_for_task(first, timeout=3)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.result["status"], "partial_success")

    async def test_account_pause_exits_attempt_instead_of_waiting_for_resume(self):
        waiting = threading.Event()

        def fetch(url, records, control):
            control.before_request()
            control.observe_result({"status": "login_required"})
            waiting.set()
            control.before_request()
            return RAW

        task_id = await self.db.create_sku_task("first")
        scheduler = self.scheduler(fetch, settings=replace(self.settings, sku_task_timeout_seconds=0.3))
        await scheduler.start()
        await eventually(waiting.is_set)
        result = await scheduler.wait_for_task(task_id, timeout=3)
        self.assertEqual(result.status, "failed")
        self.assertEqual((await self.db.get_account_state("default"))["state"], "paused")
        self.assertEqual((await self.db.get_scheduler_state()).state, "normal")
        self.assertEqual(len(await self.db.list_task_attempts(task_id)), 1)

    async def test_download_counts_budget_but_does_not_take_request_permit(self):
        upload = await self.db.create_upload_task("image")
        scheduler = self.scheduler(settings=replace(self.settings, upload_task_timeout_seconds=0.1))

        async def download(*args, **kwargs):
            await asyncio.sleep(0.2)
            return b"image"

        with patch("api.worker.download_image", download), patch.object(scheduler.executor, "upload_sync") as send:
            await scheduler.start()
            result = await scheduler.wait_for_task(upload, timeout=3)
        self.assertEqual(result.error_code, "TASK_TIMEOUT")
        send.assert_not_called()
        self.assertEqual(scheduler.guard._next_request, 0)

    async def test_cancelled_download_holds_slot_and_never_starts_upload(self):
        entered, release = asyncio.Event(), asyncio.Event()
        upload = await self.db.create_upload_task("image")
        queued = await self.db.create_sku_task("queued")
        scheduler = self.scheduler()

        async def download(*args, **kwargs):
            entered.set()
            await release.wait()
            return b"image"

        with patch("api.worker.download_image", download), patch.object(scheduler.executor, "upload_sync") as send:
            try:
                await scheduler.start()
                await asyncio.wait_for(entered.wait(), 3)
                await self.db.cancel_upload_task(upload)
                await asyncio.sleep(0.05)
                self.assertEqual((await self.db.get_queue_task(upload)).status, "running")
                self.assertEqual((await self.db.get_queue_task(queued)).status, "queued")
                await self.db.cancel_queue_task(queued)
            finally:
                release.set()
            result = await scheduler.wait_for_task(upload, timeout=3)
        self.assertEqual(result.status, "cancelled")
        send.assert_not_called()
        self.assertEqual(scheduler.guard._next_request, 0)

    async def test_global_pause_does_not_exclude_inflight_network_budget(self):
        entered, release = threading.Event(), self.event()

        def fetch(url, records, control):
            control.before_request()
            entered.set()
            release.wait(5)
            return RAW

        task_id = await self.db.create_sku_task("inflight")
        scheduler = self.scheduler(fetch, settings=replace(self.settings, sku_task_timeout_seconds=10))
        offset = 0
        scheduler.guard.clock = lambda: time.monotonic() + offset
        await scheduler.start()
        await eventually(entered.is_set)
        await self.db.pause_scheduler("other task requires login")
        offset = 11
        self.assertEqual((await self.db.get_queue_task(task_id)).status, "running")
        release.set()
        task = await scheduler.wait_for_task(task_id, timeout=3)
        self.assertEqual(task.error_code, "TASK_TIMEOUT")
        self.assertIsNone(task.result)
        self.assertEqual((await self.db.get_scheduler_state()).state, "paused")

    async def test_production_upload_product_helpers_share_guard_and_close_sessions(self):
        controls, closed = [], []
        result = SimpleNamespace(
            upload=SimpleNamespace(image_id="image-id"), found=1,
            products=[SimpleNamespace(image_url="image", title="title", price="1",
                                      sale_quantity="2", link_url="offer")],
        )

        def client_factory(**kwargs):
            control = kwargs["request_control"]
            controls.append(control)

            def upload(content):
                control.before_request()
                return SimpleNamespace(image_id="image-id")

            def search(image_id):
                control.before_request()
                return result

            return SimpleNamespace(upload_image_bytes=upload, search_image_id=search,
                                   session=SimpleNamespace(close=lambda: closed.append(True)))

        scheduler = self.scheduler()
        with patch("api.worker.ImageSearchClient", side_effect=client_factory), patch(
            "api.worker.download_image", AsyncMock(return_value=b"image")
        ):
            upload = await self.db.create_upload_task("image")
            await scheduler.start()
            uploaded = await scheduler.wait_for_task(upload, timeout=3)
            self.assertEqual(uploaded.status, "succeeded")
            product, error = await self.db.create_product_task(upload)
            self.assertIsNone(error)
            searched = await scheduler.wait_for_task(product, timeout=3)
            self.assertEqual(searched.status, "succeeded")
            self.assertEqual(searched.result["products"][0]["title"], "title")
        self.assertEqual(len(closed), 2)
        self.assertIs(controls[0].guard, controls[1].guard)
        self.cookies.load_version.assert_awaited()

    async def test_production_sku_client_receives_guard_and_raw_result_is_saved(self):
        result = SkuResult("1", "url", "access_restricted", "http_429")
        client = SimpleNamespace(fetch=lambda url: result)
        task_id = await self.db.create_sku_task("url")
        scheduler = self.scheduler()
        with patch("api.worker.ProductSkuClient") as factory:
            factory.return_value.__enter__.return_value = client
            await scheduler.start()
            record = await scheduler.wait_for_task(task_id, timeout=3)
            self.assertIsNotNone(factory.call_args.kwargs["request_control"])
        self.assertEqual(record.result, result.to_dict())
        self.assertEqual(record.status, "failed")
        self.assertEqual((await self.db.get_account_state("default"))["state"], "cooldown")
        self.assertEqual((await self.db.get_scheduler_state()).state, "normal")

    async def test_account_risk_releases_slot_retries_elsewhere_and_keeps_versions(self):
        await self.db.store_cookie(b"b", cookie_count=1, exported_at=None, account_id="other")
        task_id = await self.db.create_sku_task("risk", account_id="default")
        bound = await self.db.get_queue_task(task_id)
        await self.db.store_cookie(b"updated", cookie_count=1, exported_at=None)
        calls = []

        def fetch(url, records, control):
            control.before_request()
            calls.append(control.account_id)
            if control.account_id == "default":
                control.observe_result({"status": "login_required"})
                control.before_request()
                self.fail("paused account must not issue another request")
            return RAW

        scheduler = self.scheduler(fetch)
        await scheduler.start()
        result = await scheduler.wait_for_task(task_id, timeout=3)
        self.assertEqual(result.status, "succeeded")
        self.assertIsNone(result.error_code)
        self.assertEqual(calls, ["default", "other"])
        attempts = await self.db.list_task_attempts(task_id)
        self.assertEqual([a["status"] for a in attempts], ["failed", "succeeded"])
        self.assertEqual(attempts[0]["cookie_version"], bound.payload["cookie_version"])
        self.assertEqual((await self.db.get_account_state("default"))["state"], "paused")
        self.assertEqual((await self.db.get_account_state("other"))["state"], "normal")
        self.assertEqual((await self.db.get_scheduler_state()).state, "normal")
        await self.db.pause_account("other", "risk")
        self.assertIsNone(await self.db.create_sku_task("unavailable"))
        self.assertEqual(calls, ["default", "other"])

    async def test_product_retry_reuploads_without_changing_parent_or_sibling(self):
        await self.db.store_cookie(b"b", cookie_count=1, exported_at=None, account_id="other")
        parent = await self.db.create_upload_task("original-url", account_id="default")
        await self.db.claim_next_task()
        await self.db.finish_queue_task(parent, status="succeeded", result={"image_id": "old", "search_page_url": "old-url"})
        original = await self.db.get_upload_task(parent)
        product, _ = await self.db.create_product_task(parent)
        sibling, _ = await self.db.create_product_task(parent)
        self.assertEqual((await self.db.get_queue_task(product)).account_id, "default")
        self.assertEqual((await self.db.get_queue_task(product)).payload["cookie_version"], original.cookie_version)
        # Product creation inherits its parent and must not consume automatic rotation.
        next_id = await self.db.create_sku_task("rotation")
        self.assertEqual((await self.db.get_queue_task(next_id)).account_id, "default")
        await self.db.cancel_queue_task(next_id)
        calls = []

        def upload(records, content, control, proxy_url=""):
            control.before_request()
            calls.append(("upload", control.account_id, content))
            return {"image_id": "new", "search_page_url": "new-url"}

        def search(records, image_id, url, control, proxy_url=""):
            control.before_request()
            calls.append(("search", control.account_id, image_id))
            if control.task_id == product and control.account_id == "default":
                raise ValueError("first search failed")
            return {"image_id": image_id, "search_page_url": url, "products": [{"title": "ok"}]}

        scheduler = self.scheduler()
        with patch("api.worker.download_image", AsyncMock(return_value=b"original")) as download, patch.object(
            scheduler.executor, "upload_sync", side_effect=upload
        ), patch.object(scheduler.executor, "product_sync", side_effect=search):
            await scheduler.start()
            result = await scheduler.wait_for_task(product, timeout=3)
            other = await scheduler.wait_for_task(sibling, timeout=3)
        self.assertEqual(result.result["image_id"], "new")
        self.assertEqual(other.result["image_id"], "old")
        self.assertEqual(await self.db.get_upload_task(parent), original)
        self.assertEqual(calls[:3], [("search", "default", "old"), ("upload", "other", b"original"), ("search", "other", "new")])
        download.assert_awaited_once()
        self.assertEqual(download.call_args.args[0], "original-url")
        attempts = await self.db.list_task_attempts(product)
        self.assertEqual([a["account_id"] for a in attempts], ["default", "other"])
        self.assertEqual(attempts[1]["result"]["image_id"], "new")

    async def test_upload_retry_uses_other_account_and_second_failure_is_terminal(self):
        await self.db.store_cookie(b"b", cookie_count=1, exported_at=None, account_id="other")
        task_id = await self.db.create_upload_task("image", account_id="default")
        calls = []

        def upload(records, content, control, proxy_url=""):
            control.before_request()
            calls.append(control.account_id)
            raise ValueError("failed " + control.account_id)

        scheduler = self.scheduler()
        with patch("api.worker.download_image", AsyncMock(return_value=b"image")) as download, patch.object(
            scheduler.executor, "upload_sync", side_effect=upload
        ):
            await scheduler.start()
            result = await scheduler.wait_for_task(task_id, timeout=3)
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, ["default", "other"])
        self.assertEqual(download.await_count, 2)
        self.assertEqual(len(await self.db.list_task_attempts(task_id)), 2)

    async def test_legacy_pools_cannot_start_and_duplicate_scheduler_rejected(self):
        for cls in (UploadWorkerPool, ProductWorkerPool):
            with self.assertRaises(RuntimeError):
                await cls(self.db, self.cookies, self.settings).start()
        scheduler = self.scheduler()
        await scheduler.start()
        duplicate = UnifiedQueueScheduler(self.db, self.cookies, self.settings)
        with self.assertRaises(RuntimeError):
            await duplicate.start()
        await scheduler.start()
        self.assertEqual((await scheduler.snapshot())["capacity"], 100)
