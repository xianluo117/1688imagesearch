"""Offline HTTP amount presentation and exact-data isolation regressions."""
from dataclasses import replace
from decimal import localcontext
import unittest

from pydantic import TypeAdapter

from api.sku_price_presentation import present_price
from api.sku_schemas import ProductSkuResponse
from api.sku_v2_converter import convert_result
from image_search.parser import parse_page
from product_sku.models import Sku
from product_sku.parser import parse_detail
from test_product_sku import URL, page
from test_sku_quote_sources import quoted


CASES = (
    ("40.00", "40"), ("40", "40"), ("39.01", "40"),
    ("39.8", "40"), ("39.80", "40"), ("39.999", "40"),
    ("0.01", "1"), ("0.000000000001", "1"),
    ("1234567890123456789012345678901234567890.000000000001",
     "1234567890123456789012345678901234567891"),
    ("9" * 40 + ".999999999999", "1" + "0" * 40),
    ("9" * 40 + ".000000000000", "9" * 40),
    ("39." + "9" * 24, "40"), (None, None),
)


class PricePresentationTests(unittest.TestCase):
    def test_ceiling_without_float_or_scientific_notation(self):
        with localcontext() as context:
            context.prec = 2
            for raw, expected in CASES:
                with self.subTest(raw=raw):
                    self.assertEqual(present_price(raw), expected)
            self.assertEqual(context.prec, 2)

    def test_both_models_same_output_and_internal_data_unchanged(self):
        original = parse_detail(page(quoted()[0]), URL)
        for raw, expected in CASES:
            with self.subTest(raw=raw):
                original.skus = [replace(original.skus[0], price=raw)]
                before = original.to_dict()
                v1 = ProductSkuResponse.model_validate(before)
                self.assertEqual(v1.skus[0].price, raw)
                self.assertEqual(v1.model_dump(mode="json")["skus"][0]["price"], expected)
                self.assertEqual(convert_result(original).data.skus[0].price.amount, expected)
                self.assertEqual(TypeAdapter(Sku).dump_python(original.skus[0])["price"], raw)
                self.assertEqual(original.to_dict(), before)

    def test_legacy_invalid_handling_unchanged(self):
        original = parse_detail(page(quoted()[0]), URL)
        for raw in ("0", "0.00", "-1", "NaN", "Infinity", "1e2", " 39.80", "+39.80", "", "1" * 41, "1." + "1" * 25):
            with self.subTest(raw=raw):
                original.skus = [replace(original.skus[0], price=raw)]
                self.assertEqual(present_price(raw), raw)
                self.assertEqual(ProductSkuResponse.model_validate(original.to_dict()).model_dump(mode="json")["skus"][0]["price"], raw)
                price = convert_result(original).data.skus[0].price
                self.assertEqual(price.model_dump(), {"amount": None, "currency": None, "status": "unavailable", "source": None, "basis": None})

    def test_exact_conflict_not_merged_by_ceiling(self):
        first, _, _ = quoted([{"skuId": "901", "price": "39.01"}])
        second, _, _ = quoted([{"skuId": "901", "price": "39.80"}])
        original = parse_detail(page([first, second]), URL)
        self.assertIsNone(original.skus[0].price)
        self.assertIn("conflicting_sku_price", original.warnings)
        self.assertIsNone(ProductSkuResponse.model_validate(original.to_dict()).model_dump(mode="json")["skus"][0]["price"])
        self.assertIsNone(convert_result(original).data.skus[0].price.amount)

    def test_image_search_and_raw_results_preserve_exact_amount(self):
        item = {"offerId": "123", "priceInfo": {"price": "39.80"}}
        payload = {"OFFER": {"items": [item], "hasMore": False}}
        product = parse_page(payload, include_raw=True).products[0]
        self.assertEqual(product.price, "39.80")
        self.assertEqual(product.to_dict(include_raw=True)["raw"], item)
        self.assertEqual(payload["OFFER"]["items"][0]["priceInfo"]["price"], "39.80")

    def test_v1_sku_schema_fields_remain_compatible(self):
        schema = ProductSkuResponse.model_json_schema(mode="serialization")
        sku_schema = schema["$defs"]["Sku"]
        self.assertEqual(set(sku_schema["properties"]), {"sku_id", "specifications", "spec_id", "price", "currency", "price_source", "price_basis"})
        self.assertEqual(sku_schema["properties"]["price"]["anyOf"], [{"type": "string"}, {"type": "null"}])
