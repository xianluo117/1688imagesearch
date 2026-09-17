"""Offline HTTP and thread-lifecycle tests; never contact 1688."""
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from cryptography.fernet import Fernet

from api.app import create_app
from api.config import ConfigurationError, Settings
from api.cookie_store import CookieStoreError
from product_sku.models import Sku, SkuResult, Specification
from product_sku.client import ProductSkuClient

URL = "https://detail.1688.com/offer/898728774563.html"
SEARCH_KEY = "search-key-abcdefghijklmnopqrstuvwxyz"
UPLOAD_KEY = "upload-key-abcdefghijklmnopqrstuvwxyz"
SECRET = "synthetic-cookie-secret-do-not-expose"


def result(status="partial_success"):
    return SkuResult(
        "898728774563", URL, status, "sku_data_found",
        skus=[Sku("123456", [Specification(1, "sku1", "蓝色")])],
        warnings=["specification_names_unverified", "sku_completeness_unknown"],
    )


class ProductSkuApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            cookie_upload_api_key=UPLOAD_KEY, search_api_key=SEARCH_KEY,
            cookie_encryption_key=Fernet.generate_key().decode("ascii"),
            database_path=Path(self.temp.name) / "sku-api.db", sku_max_concurrency=1,
        )
        self.app = create_app(self.settings)
        # Exercise real startup/stop without permitting graph-search workers to
        # make network requests, even if a regression accidentally creates tasks.
        self.patches = [
            patch.object(self.app.state.upload_workers, "start", AsyncMock()),
            patch.object(self.app.state.product_workers, "start", AsyncMock()),
            patch("api.sku_service.ProductSkuClient"),
        ]
        for p in self.patches:
            value = p.start()
        self.factory = value
        self.fake = self.factory.return_value
        self.fake.__enter__.return_value = self.fake
        self.fake.fetch.return_value = result()
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://testserver",
        )
        self.release = threading.Event()

    async def asyncTearDown(self):
        self.release.set()
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    async def save_cookie(self):
        await self.app.state.cookie_store.save({"cookies": [
            {"name": name, "value": SECRET, "domain": ".1688.com", "path": "/"}
            for name in ("cookie1", "_m_h5_tk", "_m_h5_tk_enc")
        ]})

    async def post(self, body=None, key=SEARCH_KEY):
        return await self.client.post(
            "/api/v1/product-skus", json=body if body is not None else {"product_url": URL},
            headers={"X-API-Key": key} if key else {},
        )

    async def wait_for(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(poll(), 3)

    async def test_search_key_required_and_upload_key_rejected(self):
        for key in (None, "wrong", UPLOAD_KEY):
            response = await self.post(key=key)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_API_KEY")
        self.factory.assert_not_called()

    async def test_missing_or_corrupt_cookie_is_safe_503(self):
        response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "COOKIE_UNAVAILABLE")
        with patch.object(self.app.state.cookie_store, "load_active", AsyncMock(
            side_effect=CookieStoreError(SECRET),
        )):
            response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET, response.text)
        self.factory.assert_not_called()

    async def test_invalid_url_and_request_do_not_load_cookie(self):
        bodies = [
            {}, {"product_url": 123}, {"product_url": ""},
            {"product_url": URL, "cookie": "not-accepted"},
            *({"product_url": url} for url in (
                "http://127.0.0.1/offer/123.html", "https://example.com/offer/123.html",
                "https://user:password@detail.1688.com/offer/123.html",
                "https://detail.1688.com:443/offer/123.html",
                "https://detail.1688.com/offer/not-a-number.html",
            )),
        ]
        with patch.object(self.app.state.cookie_store, "load_active", AsyncMock()) as load:
            for body in bodies:
                response = await self.post(body)
                self.assertEqual(response.status_code, 422, response.text)
            load.assert_not_called()
        self.factory.assert_not_called()

    async def test_success_partial_result_canonical_url_and_no_image_tasks(self):
        await self.save_cookie()
        for status in ("success", "partial_success"):
            self.fake.fetch.return_value = result(status)
            response = await self.post({"product_url": URL.replace("https:", "http:") + "?a=1#x"})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["status"], status)
            self.assertEqual(payload["sku_count"], 1)
            self.assertEqual(payload["warnings"], result().warnings)
            self.assertEqual(payload["skus"][0]["specifications"][0]["value"], "蓝色")
            self.assertIsNone(payload["skus"][0]["specifications"][0]["name"])
            self.assertEqual(payload["completeness"], "unknown")
            self.assertNotIn(SECRET, response.text)
        self.fake.fetch.assert_called_with(URL)
        self.assertEqual(self.fake.__exit__.call_count, 2)
        kwargs = self.factory.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 20)
        self.assertEqual(kwargs["network_retries"], 1)
        self.assertEqual(kwargs["cookie_records"][0].value, SECRET)
        self.assertEqual(sum((await self.app.state.database.upload_task_counts()).values()), 0)
        self.assertEqual(sum((await self.app.state.database.product_task_counts()).values()), 0)

    async def test_real_client_parse_and_owned_session_close(self):
        await self.save_cookie()
        self.factory.side_effect = ProductSkuClient
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.url = URL
        upstream.headers = {}
        upstream.iter_content.return_value = [
            b'<script type="application/json">{"offerId":"898728774563",'
            b'"pieceWeightScaleInfo":[{"skuId":"123456","sku1":"M"}]}</script>'
        ]
        with patch("product_sku.client.requests.Session") as session_factory:
            session = session_factory.return_value
            session.get.return_value = upstream
            response = await self.post()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "partial_success")
            self.assertEqual(response.json()["skus"][0]["sku_id"], "123456")
            self.assertIn("specification_names_unverified", response.json()["warnings"])
            self.assertNotIn(SECRET, response.text)
            session.close.assert_called_once()
            upstream.close.assert_called_once()
            self.assertEqual(session.get.call_args.kwargs["timeout"], 20)

    async def test_seller_ids_preserved_and_missing_are_null(self):
        await self.save_cookie()
        for user_id, member_id in (("987654321012345678", "synthetic_member-01"), (None, None)):
            payload = result()
            payload.seller_user_id = user_id
            payload.seller_member_id = member_id
            self.fake.fetch.return_value = payload
            response = await self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["seller_user_id"], user_id)
            self.assertEqual(response.json()["seller_member_id"], member_id)
            self.assertNotIn("buyer_user_id", response.json())
            self.assertNotIn("seller_login_id", response.json())
        properties = self.app.openapi()["components"]["schemas"]["ProductSkuResponse"]["properties"]
        self.assertIn("seller_user_id", properties)
        self.assertIn("seller_member_id", properties)

    async def test_classified_failures_keep_result_and_warnings(self):
        await self.save_cookie()
        for status, code in (
            ("access_restricted", 502), ("login_required", 503),
            ("source_not_applicable", 200), ("parse_failed", 502), ("network_failed", 502),
        ):
            self.fake.fetch.return_value = SkuResult(
                "898728774563", URL, status, "fixed_reason", warnings=["fixed_warning"],
            )
            response = await self.post()
            self.assertEqual(response.status_code, code, response.text)
            self.assertEqual(response.json()["status"], status)
            self.assertEqual(response.json()["reason"], "fixed_reason")
            self.assertEqual(response.json()["warnings"], ["fixed_warning"])

    async def test_unusable_cookie_constructor_and_exception_redaction(self):
        await self.save_cookie()
        self.factory.side_effect = ValueError(SECRET)
        response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(SECRET, response.text)
        self.factory.side_effect = None
        self.fake.fetch.side_effect = RuntimeError(SECRET)
        response = await self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(SECRET, response.text)
        self.fake.__exit__.assert_called_once()
        self.fake.fetch.side_effect = None
        self.assertEqual((await self.post()).status_code, 200)

    def block_fetch(self):
        started = threading.Event()
        def fetch(_url):
            started.set()
            if not self.release.wait(5):
                raise RuntimeError("test release timeout")
            return result()
        self.fake.fetch.side_effect = fetch
        return started

    async def test_busy_cancel_keeps_slot_until_thread_and_session_finish(self):
        await self.save_cookie()
        started = self.block_fetch()
        first = asyncio.create_task(self.post())
        await self.wait_for(started.is_set)
        busy = await self.post()
        self.assertEqual(busy.status_code, 429)
        self.assertEqual(busy.json()["detail"]["code"], "SKU_BUSY")
        # The event loop remains usable while the network worker is blocked.
        self.assertEqual((await self.client.get("/health")).status_code, 200)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual((await self.post()).status_code, 429)
        self.fake.__exit__.assert_not_called()
        self.assertEqual(self.factory.call_count, 1)
        self.release.set()
        await self.wait_for(lambda: not self.app.state.sku_service._jobs)
        self.fake.__exit__.assert_called_once()
        self.assertEqual((await self.post()).status_code, 200)

    async def test_timeout_keeps_slot_and_shutdown_drains_thread(self):
        await self.save_cookie()
        self.app.state.sku_service.settings = replace(self.settings, sku_query_timeout_seconds=0.05)
        started = self.block_fetch()
        first = asyncio.create_task(self.post())
        await self.wait_for(started.is_set)
        response = await first
        self.assertEqual(response.status_code, 504)
        self.assertEqual((await self.post()).status_code, 429)
        stop = asyncio.create_task(self.app.state.sku_service.stop())
        await asyncio.sleep(0.02)
        self.assertFalse(stop.done())
        self.assertEqual((await self.post()).status_code, 503)
        self.release.set()
        await asyncio.wait_for(stop, 3)
        self.fake.__exit__.assert_called_once()

    async def test_openapi_has_typed_route(self):
        schema = self.app.openapi()
        operation = schema["paths"]["/api/v1/product-skus"]["post"]
        self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/ProductSkuResponse"})

    def test_sku_settings_limits(self):
        for changes in (
            {"sku_max_concurrency": 0}, {"sku_max_concurrency": 9},
            {"sku_http_timeout": 121}, {"sku_http_timeout": float("nan")},
            {"sku_query_timeout_seconds": float("inf")}, {"sku_query_timeout_seconds": 0},
        ):
            with self.assertRaises(ConfigurationError):
                replace(self.settings, **changes)


if __name__ == "__main__":
    unittest.main()
