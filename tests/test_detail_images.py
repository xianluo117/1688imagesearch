"""Description parsing, optional fetch failures and API round trips."""
import json
import unittest
from http.cookiejar import CookieJar
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.sku_schemas import ProductSkuResponse
from api.sku_service import restore_result
from api.sku_v2_converter import convert_result
from product_sku.client import ProductSkuClient
from product_sku.detail_images import description_url, enrich_detail_images, parse_description
from product_sku.models import Sku, SkuResult, Specification
from request_control import control_scope


URL = "https://detail.1688.com/offer/123.html"
DESCRIPTION = "https://itemcdn.tmall.com/1688offer/example"
IMAGE = "https://cbu01.alicdn.com/a.jpg"


def page(product_id="123"):
    root = {"result": {
        "global": {"globalData": {
            "parametersMap": {"offerId": product_id},
            "model": {"offerDetail": {"offerId": product_id}, "tradeModel": {"offerId": product_id}},
        }},
        "data": {"description": {"fields": {"detailUrl": DESCRIPTION}}},
    }}
    return '<script type="application/json">' + json.dumps(root) + '</script>'


def result():
    return SkuResult("123", URL, "partial_success", "sku_data_found",
                     skus=[Sku("456", [Specification(1, "sku1", "red")])])


def client(body=None, status=200):
    response = Mock(status_code=status)
    response.iter_content.return_value = [body or ('<img src="' + IMAGE + '">').encode()]
    instance = SimpleNamespace(session=Mock(), request_control=None, timeout=20, max_bytes=4096)
    instance.session.get.return_value = response
    return instance, response


class DetailImageTests(unittest.TestCase):
    def test_html_order_lazy_and_deduplication(self):
        html = '<img src="//cbu01.alicdn.com/a.jpg"><img src="' + IMAGE + '">'
        html += '<img data-src="https://cbu01.alicdn.com/b.jpg?a=1&b=2" src="placeholder.gif">'
        self.assertEqual(parse_description(html), [IMAGE, "https://cbu01.alicdn.com/b.jpg?a=1&b=2"])

    def test_assignment_and_json(self):
        payload = json.dumps({"content": '<img src="' + IMAGE + '">'})
        for text in (payload, "var offer_details=" + payload + ";", "\ufeff offer_details = " + payload):
            self.assertEqual(parse_description(text), [IMAGE])

    def test_malformed_assignment(self):
        with self.assertRaises(ValueError):
            parse_description('var offer_details={broken')

    def test_verified_description_source(self):
        self.assertEqual(description_url(page(), "123"), DESCRIPTION)
        self.assertIsNone(description_url(page("999"), "123"))

    def test_fetch_and_round_trip(self):
        instance, response = client()
        enriched = enrich_detail_images(instance, page(), result())
        self.assertEqual(enriched.detail_images, [IMAGE])
        response.close.assert_called_once()
        instance.session.get.assert_called_once()
        restored = restore_result(enriched.to_dict())
        self.assertEqual(ProductSkuResponse.model_validate(restored.to_dict()).detail_images, [IMAGE])
        self.assertEqual(convert_result(restored).data.product.detail_images, [IMAGE])

    def test_legacy_result(self):
        payload = result().to_dict()
        del payload["detail_images"]
        restored = restore_result(payload)
        self.assertEqual(restored.detail_images, [])
        self.assertEqual(ProductSkuResponse.model_validate(payload).detail_images, [])
        self.assertEqual(convert_result(restored).data.product.detail_images, [])

    def test_failures_do_not_change_product_result(self):
        for kind in ("http", "network", "malformed", "size"):
            with self.subTest(kind=kind):
                instance, response = client()
                if kind == "http":
                    response.status_code = 503
                elif kind == "network":
                    instance.session.get.side_effect = TimeoutError()
                elif kind == "malformed":
                    response.iter_content.return_value = [b'var offer_details=bad']
                else:
                    instance.max_bytes = 1
                original = result()
                before = original.to_dict()
                enriched = enrich_detail_images(instance, page(), original)
                after = enriched.to_dict()
                self.assertEqual(after.pop("warnings"), ["detail_images_fetch_failed"])
                before.pop("warnings")
                self.assertEqual(after, before)
                instance.session.get.assert_called_once()

    def test_missing_description(self):
        instance, _ = client()
        enriched = enrich_detail_images(instance, page("999"), result())
        self.assertEqual(enriched.detail_images, [])
        self.assertIn("detail_images_unavailable", enriched.warnings)
        instance.session.get.assert_not_called()

    def test_empty_description(self):
        instance, _ = client(b"<div>text only</div>")
        enriched = enrich_detail_images(instance, page(), result())
        self.assertEqual(enriched.detail_images, [])
        self.assertIn("detail_images_unavailable", enriched.warnings)

    def test_control_abort_is_preserved(self):
        instance, _ = client()
        instance.request_control = Mock()
        instance.request_control.before_request.side_effect = RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"), control_scope():
            enrich_detail_images(instance, page(), result())
        instance.session.get.assert_not_called()

    def test_client_fetch_enriches_after_detail_parse(self):
        detail = Mock(status_code=200, url=URL, headers={})
        detail.iter_content.return_value = [page().encode()]
        instance, description = client()
        session = instance.session
        session.get.side_effect = [detail, description]
        with patch("product_sku.client.detail_cookie_jar", return_value=CookieJar()):
            with ProductSkuClient(cookie_records=[], session=session, network_retries=0) as product_client:
                with patch("product_sku.client.parse_detail", return_value=result()):
                    enriched = product_client.fetch(URL)
        self.assertEqual(enriched.detail_images, [IMAGE])
        self.assertEqual(session.get.call_count, 2)
        detail.close.assert_called_once()
        description.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
