"""Offline application integration: real SQLite, lifecycle and sole scheduler."""
import asyncio
from dataclasses import replace
import time
import unittest
from unittest.mock import AsyncMock, patch

from api.app import create_app
from api.sku_schemas import ProductSkuResponse
from api.sku_v2_converter import convert_result
import test_sku_api as fixtures

SKU = "/api/v2/sku-tasks"
QUEUE = "/api/v1/queue"
HEADERS = {"X-API-Key": fixtures.SEARCH_KEY}


class UnifiedQueueApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ProductSkuApiTests.asyncSetUp
    asyncTearDown = fixtures.ProductSkuApiTests.asyncTearDown
    save_cookie = fixtures.ProductSkuApiTests.save_cookie
    wait_for = fixtures.ProductSkuApiTests.wait_for
    block_fetch = fixtures.ProductSkuApiTests.block_fetch

    async def submit(self, *, key=None, url=fixtures.URL):
        headers = dict(HEADERS)
        if key is not None:
            headers["Idempotency-Key"] = key
        return await self.client.post(SKU, headers=headers, json={"product_url": url})

    async def poll(self, task_id):
        await self.app.state.scheduler.wait_for_task(task_id, timeout=3)
        return await self.client.get(f"{SKU}/{task_id}", headers=HEADERS)

    async def test_async_success_v2_and_no_implicit_url_cache(self):
        await self.save_cookie()
        created = await self.submit()
        self.assertEqual(created.status_code, 202, created.text)
        task_id = created.json()["task_id"]
        self.assertEqual(created.headers["location"], f"{SKU}/{task_id}")
        response = await self.poll(task_id)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["result"], convert_result(fixtures.result()).model_dump(mode="json"))
        self.assertEqual(body["scheduler"]["state"], "normal")
        second = await self.submit()
        self.assertNotEqual(second.json()["task_id"], task_id)
        await self.poll(second.json()["task_id"])
        self.assertEqual(self.fake.fetch.call_count, 2)
        self.assertFalse(hasattr(self.app.state, "upload_workers"))
        self.assertFalse(hasattr(self.app.state, "product_workers"))
        self.assertIs(self.app.state.database._scheduler_owner, self.app.state.scheduler)

    async def test_auth_for_all_queue_routes_and_kind_isolation(self):
        await self.save_cookie()
        await self.app.state.database.pause_scheduler("test")
        image = await self.app.state.database.create_upload_task("https://example.com/a.jpg", max_active=10)
        for method, path in (("POST", SKU), ("GET", SKU + "/missing"),
                             ("DELETE", SKU + "/missing"), ("GET", QUEUE), ("POST", QUEUE + "/resume")):
            for key in (None, "wrong", fixtures.UPLOAD_KEY):
                response = await self.client.request(method, path,
                    headers={"X-API-Key": key} if key else {}, json={"product_url": fixtures.URL})
                self.assertEqual(response.status_code, 401)
        for task_id in ("missing", image):
            for method in ("GET", "DELETE"):
                response = await self.client.request(method, SKU + "/" + task_id, headers=HEADERS)
                self.assertEqual(response.status_code, 404)
        self.assertFalse((await self.app.state.database.get_queue_task(image)).cancel_requested)
        self.fake.fetch.assert_not_called()

    async def test_idempotency_normalization_conflict_and_cross_version_replay(self):
        await self.save_cookie()
        first = await self.submit(key="same", url=fixtures.URL.replace("https:", "http:") + "?a=1")
        task_id = first.json()["task_id"]
        await self.poll(task_id)
        self.assertEqual((await self.submit(key="same")).json()["task_id"], task_id)
        conflict = await self.submit(key="same", url="https://detail.1688.com/offer/123.html")
        self.assertEqual(conflict.status_code, 409)
        for version in (1, 2):
            response = await self.client.post(f"/api/v{version}/product-skus",
                headers={**HEADERS, "Idempotency-Key": "same"}, json={"product_url": fixtures.URL})
            expected = (ProductSkuResponse.model_validate(fixtures.result().to_dict()) if version == 1
                        else convert_result(fixtures.result()))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), expected.model_dump(mode="json"))
        self.fake.fetch.assert_called_once()
        for key in ("", " " * 3, "x" * 256):
            self.assertEqual((await self.submit(key=key)).status_code, 422)

    async def test_restart_keeps_pause_tasks_and_idempotency(self):
        await self.save_cookie()
        await self.app.state.database.pause_scheduler("verification_required")
        created = await self.submit(key="restart")
        task_id = created.json()["task_id"]
        await self.lifespan.__aexit__(None, None, None)
        self.app = create_app(self.settings)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        import httpx
        await self.client.aclose()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        replay = await self.submit(key="restart")
        self.assertEqual(replay.json()["task_id"], task_id)
        self.assertEqual(replay.json()["status"], "queued")
        self.assertEqual(replay.json()["scheduler"]["reason"], "verification_required")
        await self.save_cookie()
        state = (await self.client.get(QUEUE, headers=HEADERS)).json()
        self.assertEqual(state["scheduler"]["state"], "paused")
        self.fake.fetch.assert_not_called()
        response = await self.client.post(QUEUE + "/resume", headers=HEADERS)
        self.assertEqual(response.json()["scheduler"]["state"], "normal")
        self.assertEqual((await self.poll(task_id)).json()["status"], "succeeded")

    async def test_business_failure_preserved_and_pause_visible(self):
        await self.save_cookie()
        original = fixtures.result("login_required")
        original.skus = []
        self.fake.fetch.return_value = original
        created = await self.submit()
        body = (await self.poll(created.json()["task_id"])).json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["result"]["status"], "login_required")
        self.assertEqual(body["scheduler"]["state"], "paused")
        queued = await self.submit()
        self.assertEqual(queued.json()["status"], "queued")
        response = await self.client.delete(queued.headers["location"], headers=HEADERS)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertTrue(response.json()["cancel_requested"])
        self.fake.fetch.assert_called_once()

    async def test_running_cancel_keeps_capacity_until_actual_return(self):
        await self.save_cookie()
        self.app.state.database.queue_capacity = 1
        started = self.block_fetch()
        created = await self.submit()
        await self.wait_for(started.is_set)
        response = await self.client.delete(created.headers["location"], headers=HEADERS)
        self.assertEqual(response.json()["status"], "running")
        self.assertTrue(response.json()["cancel_requested"])
        self.assertEqual((await self.submit()).status_code, 429)
        self.fake.__exit__.assert_not_called()
        self.release.set()
        body = (await self.poll(created.json()["task_id"])).json()
        self.assertEqual(body["status"], "cancelled")
        self.assertIsNone(body["result"])
        repeated = await self.client.delete(created.headers["location"], headers=HEADERS)
        self.assertEqual(repeated.json()["status"], "cancelled")

    async def test_mixed_capacity_image_status_and_idempotency(self):
        await self.save_cookie()
        database = self.app.state.database
        database.queue_capacity = 2
        await database.set_cooldown("rate_limited", until=time.time() + 60)
        image = await self.client.post("/api/v1/upload-tasks", headers={**HEADERS, "Idempotency-Key": "image"},
            json={"image_url": "https://example.com/a.jpg"})
        self.assertEqual(image.status_code, 202)
        sku = await self.submit()
        self.assertEqual(sku.status_code, 202)
        self.assertEqual((await self.submit()).status_code, 429)
        image_id = image.json()["task_id"]
        fetched = await self.client.get("/api/v1/upload-tasks/" + image_id, headers=HEADERS)
        self.assertEqual(fetched.json()["scheduler"]["state"], "cooldown")
        self.assertGreater(fetched.json()["scheduler"]["cooldown_remaining"], 0)
        replay = await self.client.post("/api/v1/upload-tasks", headers={**HEADERS, "Idempotency-Key": "image"},
            json={"image_url": "https://example.com/a.jpg"})
        self.assertEqual(replay.json()["task_id"], image_id)
        self.assertEqual((await self.submit(key="image")).status_code, 409)
        snapshot = (await self.client.get(QUEUE, headers=HEADERS)).json()
        self.assertEqual(snapshot["counts"]["queued"], 2)
        self.assertEqual(snapshot["global_workers"], 1)
        await self.client.delete("/api/v1/upload-tasks/" + image_id, headers=HEADERS)
        replay = await self.client.post("/api/v1/upload-tasks", headers={**HEADERS, "Idempotency-Key": "image"},
            json={"image_url": "https://example.com/a.jpg"})
        self.assertEqual(replay.json()["status"], "cancelled")

    async def test_shutdown_preserves_queued_and_rejects_submission(self):
        await self.save_cookie()
        await self.app.state.database.pause_scheduler("test")
        task_id = (await self.submit()).json()["task_id"]
        await self.app.state.scheduler.stop()
        self.assertEqual((await self.submit()).status_code, 503)
        image = await self.client.post("/api/v1/upload-tasks", headers=HEADERS,
            json={"image_url": "https://example.com/a.jpg"})
        self.assertEqual(image.status_code, 503)
        self.assertEqual((await self.app.state.database.get_queue_task(task_id)).status, "queued")
        self.fake.fetch.assert_not_called()

    async def test_all_three_kinds_use_application_scheduler(self):
        await self.save_cookie()
        scheduler = self.app.state.scheduler
        await self.app.state.database.pause_scheduler("arrange")
        order = []

        def upload(records, content, control):
            order.append("upload")
            self.assertIs(control.guard, scheduler.guard)
            return {"image_id": "image", "search_page_url": "https://example.com/search"}

        def product(records, image_id, search_page_url, control):
            order.append("product")
            self.assertIs(control.guard, scheduler.guard)
            return {"image_id": image_id, "search_page_url": search_page_url, "found": 0, "products": []}

        def sku(url):
            order.append("sku")
            return fixtures.result()

        self.fake.fetch.side_effect = sku
        with patch("api.worker.download_image", AsyncMock(return_value=b"image")), patch.object(
            scheduler.executor, "upload_sync", side_effect=upload
        ), patch.object(scheduler.executor, "product_sync", side_effect=product):
            uploaded = await self.client.post("/api/v1/upload-tasks", headers=HEADERS,
                json={"image_url": "https://example.com/a.jpg"})
            upload_id = uploaded.json()["task_id"]
            premature = await self.client.post("/api/v1/product-tasks", headers=HEADERS,
                json={"upload_task_id": upload_id})
            self.assertEqual(premature.status_code, 409)
            sku_id = (await self.submit()).json()["task_id"]
            await scheduler.resume()
            await self.poll(sku_id)
            created = await self.client.post("/api/v1/product-tasks",
                headers={**HEADERS, "Idempotency-Key": "product"}, json={"upload_task_id": upload_id})
            self.assertEqual(created.status_code, 202)
            product_id = created.json()["task_id"]
            await scheduler.wait_for_task(product_id, timeout=3)
            status = await self.client.get("/api/v1/product-tasks/" + product_id, headers=HEADERS)
            self.assertEqual(status.json()["status"], "succeeded")
            self.assertEqual(status.json()["scheduler"]["state"], "normal")
            replay = await self.client.post("/api/v1/product-tasks",
                headers={**HEADERS, "Idempotency-Key": "product"}, json={"upload_task_id": upload_id})
            self.assertEqual(replay.json()["task_id"], product_id)
            self.assertEqual(replay.json()["status"], "succeeded")
        self.assertEqual(order, ["upload", "sku", "product"])
        self.assertIs(self.factory.call_args.kwargs["request_control"].guard, scheduler.guard)

    async def test_raw_persisted_price_and_nested_fields_roundtrip(self):
        from product_sku.models import ColorSizes, SizeDimension, Specification, SpecificationImage
        await self.save_cookie()
        original = fixtures.result()
        color = Specification(1, "sku1", "蓝色", "颜色")
        original.skus = [replace(original.skus[0], specifications=[color], price="39.01", currency="CNY")]
        original.sizes = [SizeDimension(2, "sku2", "尺码", ["M"])]
        original.color_sizes = [ColorSizes(color, 2, "sku2", "尺码", ["M"])]
        original.specification_images = [SpecificationImage(1, "sku1", "颜色", "蓝色", "https://example.com/blue.jpg")]
        self.fake.fetch.return_value = original
        task_id = (await self.submit(key="raw")).json()["task_id"]
        await self.poll(task_id)
        record = await self.app.state.database.get_queue_task(task_id)
        self.assertEqual(record.result, original.to_dict())
        for version in (1, 2):
            response = await self.client.post(f"/api/v{version}/product-skus",
                headers={**HEADERS, "Idempotency-Key": "raw"}, json={"product_url": fixtures.URL})
            expected = ProductSkuResponse.model_validate(original.to_dict()) if version == 1 else convert_result(original)
            self.assertEqual(response.json(), expected.model_dump(mode="json"))
        self.fake.fetch.assert_called_once()

    async def test_http_disconnect_does_not_cancel_queued_task(self):
        await self.save_cookie()
        await self.app.state.database.pause_scheduler("test")
        pending = asyncio.create_task(self.client.post("/api/v2/product-skus",
            headers={**HEADERS, "Idempotency-Key": "disconnect"}, json={"product_url": fixtures.URL}))
        for _ in range(100):
            if (await self.app.state.database.queue_task_counts())["queued"]:
                break
            await asyncio.sleep(0.01)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        replay = await self.submit(key="disconnect")
        self.assertFalse(replay.json()["cancel_requested"])
        await self.app.state.scheduler.resume()
        self.assertEqual((await self.poll(replay.json()["task_id"])).json()["status"], "succeeded")
