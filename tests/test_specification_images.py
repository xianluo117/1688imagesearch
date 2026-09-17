"""Synthetic option-image regression tests; no network or real image URLs."""
import copy
import unittest

from test_product_sku import URL, page, trade_page, business
from product_sku.parser import parse_detail
from product_sku.specification_images import safe_image_url

IMAGE = "https://img.example/blue.jpg"


def fixture():
    value, model = trade_page()
    model["offerDetail"]["skuProps"][0]["value"][0]["imageUrl"] = IMAGE
    model["tradeModel"]["skuMap"].append({"skuId": "902", "specAttrs": "蓝色" + chr(38) + "gt;M"})
    return value, model


class SpecificationImageTests(unittest.TestCase):
    def test_one_color_image_across_sizes(self):
        value, _ = fixture()
        result = parse_detail(page(value), URL)
        self.assertEqual(len(result.skus), 2)
        self.assertEqual(len(result.specification_images), 3)
        self.assertEqual([i.image_url for i in result.specification_images], [IMAGE, None, None])
        self.assertEqual(result.specification_images[0].name, "颜色")
        self.assertEqual(result.specification_images[0].value, "蓝色")
        self.assertNotIn("image_url", result.to_dict()["skus"][0])

    def test_missing_and_unsafe_are_null(self):
        for image in (None, "", "javascript:alert(1)", "//img.example/a.jpg", "http://127.0.0.1/a", "https://user:pass@img.example/a"):
            value, model = fixture()
            model["offerDetail"]["skuProps"][0]["value"][0]["imageUrl"] = image
            result = parse_detail(page(value), URL)
            self.assertTrue(result.ok)
            self.assertIsNone(result.specification_images[0].image_url)

    def test_conflicting_candidates_null_regardless_order(self):
        first, _ = fixture()
        second = copy.deepcopy(first)
        second["result"]["global"]["globalData"]["model"]["offerDetail"]["skuProps"][0]["value"][0]["imageUrl"] = "https://img.example/other.jpg"
        for roots in ([first, second], [second, first]):
            result = parse_detail(page(roots), URL)
            self.assertIsNone(result.specification_images[0].image_url)
            self.assertEqual(len(result.specification_images), 3)

    def test_recommendation_cannot_supply_image(self):
        value, model = fixture()
        recommended = copy.deepcopy(value)
        del model["offerDetail"]["skuProps"][0]["value"][0]["imageUrl"]
        value["recommendations"] = recommended
        result = parse_detail(page(value), URL)
        self.assertTrue(all(i.image_url is None for i in result.specification_images))

    def test_unreturned_options_and_conflicted_skus_excluded(self):
        value, model = fixture()
        model["offerDetail"]["skuProps"][0]["value"][1]["imageUrl"] = "https://img.example/red.jpg"
        result = parse_detail(page(value), URL)
        self.assertNotIn("红色", [i.value for i in result.specification_images])
        model["tradeModel"]["skuMap"].append({"skuId": "901", "specAttrs": "红色" + chr(38) + "gt;L"})
        result = parse_detail(page(value), URL)
        self.assertEqual([s.sku_id for s in result.skus], ["902"])
        self.assertNotIn("红色", [i.value for i in result.specification_images])
        self.assertNotIn("L", [i.value for i in result.specification_images])

    def test_legacy_no_image_fallback(self):
        result = parse_detail(page(business()), URL)
        self.assertTrue(result.ok)
        self.assertTrue(all(i.image_url is None for i in result.specification_images))

    def test_bad_mapping_cannot_supply_image(self):
        value, model = fixture()
        for row in model["tradeModel"]["skuMap"]:
            row["specAttrs"] = "unknown"
        result = parse_detail(page(value), URL)
        self.assertFalse(result.ok)
        self.assertEqual(result.specification_images, [])

    def test_url_validation(self):
        for value in ("data:image/png;base64,x", "file:///a", "https://img.example/a\n", "https://img.example\\evil/a", "https://img.example:invalid/a", "https://localhost/a", "http://10.0.0.1/a", "https://img.example/a#x", 123):
            self.assertIsNone(safe_image_url(value))
        self.assertEqual(safe_image_url(IMAGE), IMAGE)
        query = IMAGE + "?size=large"
        self.assertEqual(safe_image_url(query), query)
