"""Independent v2 HTTP tests; shared offline fixture never contacts upstream."""
import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from api.sku_schemas import ProductSkuResponse
from api.sku_service import SkuServiceError
from product_sku.models import SkuResult
import test_sku_api as fixtures

V2 = "/api/v2/product-skus"
V1 = "/api/v1/product-skus"


class ProductSkuApiV2Tests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ProductSkuApiTests.asyncSetUp
    asyncTearDown = fixtures.ProductSkuApiTests.asyncTearDown
    save_cookie = fixtures.ProductSkuApiTests.save_cookie
    wait_for = fixtures.ProductSkuApiTests.wait_for
    block_fetch = fixtures.ProductSkuApiTests.block_fetch

    async def post(self, body=None, key=fixtures.SEARCH_KEY, path=V2):
        return await self.client.post(
            path, json=body if body is not None else {"product_url": fixtures.URL},
            headers={"X-API-Key": key} if key else {},
        )

    async def test_authentication_and_input_no_queries(self):
        with patch.object(self.app.state.sku_service, "query", AsyncMock()) as query:
            for key in (None, "wrong", fixtures.UPLOAD_KEY):
                response = await self.post(key=key)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"]["code"], "INVALID_API_KEY")
            for body in ({}, {"product_url": 123}, {"product_url": ""}, {"product_url": fixtures.URL, "extra": True}, {"product_url": "https://example.com/offer/123.html"}, {"product_url": "https://detail.1688.com:443/offer/123.html"}):
                response = await self.post(body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("detail", response.json())
            query.assert_not_called()

    async def test_one_query_and_same_service_v1_unchanged(self):
        service = self.app.state.sku_service
        original = fixtures.result()
        before = original.to_dict()
        with patch.object(service, "query", AsyncMock(return_value=original)) as query:
            response = await self.post({"product_url": fixtures.URL.replace("https:", "http:") + "?x=1#part"})
            query.assert_awaited_once_with(fixtures.URL)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(set(payload), {"status", "data", "meta"})
            self.assertEqual(payload["meta"]["schema_version"], "2")
            self.assertEqual(payload["data"]["product"]["url"], fixtures.URL)
            self.assertEqual(payload["meta"]["warnings"], original.warnings)
            query.reset_mock()
            legacy = await self.post(path=V1)
            query.assert_awaited_once_with(fixtures.URL)
            self.assertEqual(legacy.json(), ProductSkuResponse.model_validate(before).model_dump(mode="json"))
            self.assertNotIn("data", legacy.json())
        self.assertIs(self.app.state.sku_service, service)
        self.assertEqual(original.to_dict(), before)
        self.factory.assert_not_called()

    async def test_all_business_status_http_mappings(self):
        for status, code in (("success", 200), ("partial_success", 200), ("source_not_applicable", 200), ("parse_failed", 502), ("access_restricted", 502), ("network_failed", 502), ("login_required", 503)):
            original = SkuResult("123", fixtures.URL, status, "fixed_reason", warnings=["fixed_warning"])
            with patch.object(self.app.state.sku_service, "query", AsyncMock(return_value=original)) as query:
                response = await self.post()
                query.assert_awaited_once()
            self.assertEqual(response.status_code, code)
            payload = response.json()
            self.assertEqual(payload["status"], status)
            self.assertEqual(payload["meta"]["reason"], "fixed_reason")
            self.assertEqual(payload["meta"]["warnings"], ["fixed_warning"])
            self.assertEqual(payload["data"]["skus"], [])
            self.assertNotIn("detail", payload)

    async def test_service_detail_errors_preserved(self):
        for code, status in (("SKU_BUSY", 429), ("COOKIE_UNAVAILABLE", 503), ("SKU_SERVICE_STOPPING", 503), ("SKU_QUERY_TIMEOUT", 504), ("SKU_QUERY_FAILED", 502)):
            with patch.object(self.app.state.sku_service, "query", AsyncMock(side_effect=SkuServiceError(status, code, "safe_message"))):
                response = await self.post()
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {"detail": {"code": code, "message": "safe_message"}})

    async def test_internal_exception_and_conversion_error_redacted(self):
        for value in (AsyncMock(side_effect=RuntimeError(fixtures.SECRET)), AsyncMock(return_value=object())):
            with patch.object(self.app.state.sku_service, "query", value):
                response = await self.post()
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["detail"]["code"], "SKU_QUERY_FAILED")
            self.assertNotIn(fixtures.SECRET, response.text)

    async def test_real_service_missing_cookie_and_single_fetch(self):
        self.assertEqual((await self.post()).status_code, 503)
        self.factory.assert_not_called()
        await self.save_cookie()
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        self.factory.assert_called_once()
        self.fake.fetch.assert_called_once_with(fixtures.URL)
        self.fake.__exit__.assert_called_once()
        self.assertEqual(response.json()["data"]["skus"][0]["price"]["status"], "unavailable")

    async def test_shared_concurrency_across_versions(self):
        await self.save_cookie()
        started = self.block_fetch()
        first = asyncio.create_task(self.post(path=V1))
        await self.wait_for(started.is_set)
        response = await self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"]["code"], "SKU_BUSY")
        self.release.set()
        self.assertEqual((await first).status_code, 200)
        self.fake.fetch.assert_called_once()
        await self.wait_for(lambda: not self.app.state.sku_service._jobs)
        self.assertEqual((await self.post()).status_code, 200)
        self.assertEqual(self.fake.fetch.call_count, 2)

    async def test_shared_start_limiter_timeout_and_stop(self):
        await self.save_cookie()
        service = self.app.state.sku_service
        service.settings = replace(self.settings, sku_query_timeout_seconds=0.05)
        import threading
        waiting = threading.Event()

        def wait():
            waiting.set()
            if not self.release.wait(5):
                raise RuntimeError("test release timeout")

        with patch.object(service._start_limiter, "wait", side_effect=wait) as limiter:
            first = asyncio.create_task(self.post())
            await self.wait_for(waiting.is_set)
            self.fake.fetch.assert_not_called()
            self.assertEqual((await self.post(path=V1)).status_code, 429)
            timed_out = await first
            self.assertEqual(timed_out.status_code, 504)
            self.assertEqual(timed_out.json()["detail"]["code"], "SKU_QUERY_TIMEOUT")
            self.assertEqual((await self.post()).status_code, 429)
            stop = asyncio.create_task(service.stop())
            await asyncio.sleep(0.01)
            self.assertFalse(stop.done())
            self.assertEqual((await self.post()).status_code, 503)
            self.release.set()
            await asyncio.wait_for(stop, 3)
            limiter.assert_called_once()
        self.fake.fetch.assert_called_once_with(fixtures.URL)
        self.fake.__exit__.assert_called_once()

    async def test_both_http_versions_ceil_prices_without_mutation(self):
        from test_sku_price_presentation import CASES

        original = fixtures.result()
        original.skus = [replace(original.skus[0], sku_id=str(index), price=raw,
                                 currency="CNY", price_source="synthetic", price_basis="test_only")
                         for index, (raw, _) in enumerate(CASES)]
        before = original.to_dict()
        with patch.object(self.app.state.sku_service, "query", AsyncMock(return_value=original)):
            v1 = await self.post(path=V1)
            v2 = await self.post(path=V2)
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(v2.status_code, 200)
        expected = [amount for _, amount in CASES]
        self.assertEqual([s["price"] for s in v1.json()["skus"]], expected)
        self.assertEqual([s["price"]["amount"] for s in v2.json()["data"]["skus"]], expected)
        self.assertEqual(original.to_dict(), before)
        for sku in v2.json()["data"]["skus"][:-1]:
            self.assertEqual(sku["price"]["source"], "synthetic")
            self.assertEqual(sku["price"]["basis"], "test_only")

    async def test_both_http_versions_keep_exact_price_conflict_null(self):
        from product_sku.parser import parse_detail
        from test_product_sku import page, URL
        from test_sku_quote_sources import quoted

        first, _, _ = quoted([{"skuId": "901", "price": "39.01"}])
        second, _, _ = quoted([{"skuId": "901", "price": "39.80"}])
        original = parse_detail(page([first, second]), URL)
        with patch.object(self.app.state.sku_service, "query", AsyncMock(return_value=original)):
            v1 = (await self.post(path=V1)).json()
            v2 = (await self.post(path=V2)).json()
        self.assertIsNone(v1["skus"][0]["price"])
        self.assertIsNone(v2["data"]["skus"][0]["price"]["amount"])
        self.assertEqual(v2["data"]["skus"][0]["price"]["status"], "unavailable")
        self.assertIn("conflicting_sku_price", v1["warnings"])
        self.assertEqual(v1["warnings"], v2["meta"]["warnings"])

    async def test_openapi_nested_contract(self):
        schema = self.app.openapi()
        responses = schema["paths"][V2]["post"]["responses"]
        for status in ("200", "502", "503"):
            self.assertEqual(responses[status]["content"]["application/json"]["schema"], {"$ref": "#/components/schemas/ProductSkuResponseV2"})
        models = schema["components"]["schemas"]
        for model, fields in {
            "ProductSkuResponseV2": {"status", "data", "meta"},
            "DataV2": {"product", "seller", "specifications", "size_summary", "skus"},
            "DimensionV2": {"dimension_id", "position", "name", "role", "options"},
            "PriceV2": {"amount", "currency", "status", "source", "basis"},
            "MetaV2": {"schema_version", "source", "sku_count", "completeness", "reason", "warnings"},
        }.items():
            self.assertEqual(set(models[model]["properties"]), fields)
        self.assertEqual(models["PriceV2"]["properties"]["status"]["enum"], ["available", "unavailable"])


if __name__ == "__main__":
    unittest.main()
