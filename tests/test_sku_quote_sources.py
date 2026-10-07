"""Offline production quote discovery, ownership, limits and response regressions."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from api.sku_schemas import ProductSkuResponse
from api.sku_v2_converter import convert_result
from product_sku.extraction import DecodeLimit
from product_sku.parser import _arrays, parse_detail
from product_sku.price_diagnostics import inspect
from product_sku.quote_sources import collect_quotes
from test_product_sku import PID, URL, business, page, trade_page


def quoted(rows=None):
    value, model = trade_page()
    container = {"skuMapOriginal": [{"skuId": "901", "price": "39.80"}] if rows is None else rows}
    value["result"]["data"] = {"mainPrice": {"fields": {"finalPriceModel": {"tradeWithoutPromotion": container}}}}
    return value, model, container


class QuoteDiscoveryTests(unittest.TestCase):
    def test_top_wrapped_array_and_nested_strings(self):
        value, _, _ = quoted()
        for wrapped in (value, {"payload": value}, [value],
                        json.dumps({"payload": json.dumps(value)})):
            with self.subTest(kind=type(wrapped).__name__):
                result = parse_detail(page(wrapped), URL)
                self.assertEqual([s.price for s in result.skus], ["39.80"])
                self.assertEqual(result.warnings, ["sku_completeness_unknown"])
                response = ProductSkuResponse(**result.to_dict())
                self.assertEqual(response.skus[0].price, "39.80")
                self.assertEqual(response.model_dump(mode="json")["skus"][0]["price"], "40")
                converted = convert_result(result)
                self.assertEqual(converted.data.skus[0].price.amount, "40")
                self.assertEqual(converted.data.skus[0].price.status, "available")

    def test_encoded_container(self):
        value, _, container = quoted()
        container["skuMapOriginal"] = json.dumps(json.dumps(container["skuMapOriginal"]))
        self.assertEqual(parse_detail(page(value), URL).skus[0].price, "39.80")

    def test_recommendations_and_other_products(self):
        good, _, _ = quoted()
        bad, model, _ = quoted([{"skuId": "901", "price": "999"}])
        for branch in ("recommendations", "related", "similar", "guess", "suggest", "hotOffer"):
            result = parse_detail(page({"payload": good, branch: bad}), URL)
            self.assertEqual(result.skus[0].price, "39.80")
        model["offerDetail"]["offerId"] = "123"
        self.assertEqual(parse_detail(page([good, bad]), URL).skus[0].price, "39.80")

    def test_adjacent_unverified_price_not_spliced(self):
        value, _ = trade_page()
        quote, _, _ = quoted()
        result = parse_detail(page({"payload": value, "adjacent": {"result": {"data": quote["result"]["data"]}}}), URL)
        self.assertIsNone(result.skus[0].price)
        self.assertIn("sku_price_source_missing", result.warnings)

    def test_source_absent_null_and_empty(self):
        for mode in ("absent", "null", "empty"):
            value, _, container = quoted()
            if mode == "absent":
                del container["skuMapOriginal"]
            else:
                container["skuMapOriginal"] = None if mode == "null" else []
            result = parse_detail(page(value), URL)
            self.assertEqual(len(result.skus), 1)
            self.assertIn("sku_price_missing", result.warnings)
            self.assertEqual("sku_price_source_missing" in result.warnings, mode != "empty")

    def test_invalid_schema_mapping_and_rows(self):
        for rows in ({"901": {"price": "39.80"}}, 12, "unsupported"):
            value, _, _ = quoted(rows)
            result = parse_detail(page(value), URL)
            self.assertIn("invalid_sku_price_schema", result.warnings)
            self.assertIsNone(result.skus[0].price)
        for row in (None, [], {"skuId": True, "price": "39.80"}, {"skuId": "901", "offerId": "123", "price": "39.80"}):
            value, _, _ = quoted([row])
            result = parse_detail(page(value), URL)
            self.assertIn("invalid_sku_price_rows", result.warnings)
            self.assertIsNone(result.skus[0].price)

    def test_unmatched_and_partial_missing(self):
        value, _, _ = quoted([{"skuId": "999", "price": "39.80"}])
        result = parse_detail(page(value), URL)
        self.assertIn("sku_price_unmatched", result.warnings)
        value, model, _ = quoted()
        model["tradeModel"]["skuMap"].append({"skuId": "902", "specAttrs": "蓝色" + chr(38) + "gt;M"})
        result = parse_detail(page(value), URL)
        self.assertEqual([s.price for s in result.skus], ["39.80", None])
        self.assertIn("sku_price_missing", result.warnings)
        converted = convert_result(result)
        self.assertEqual(converted.meta.warnings, result.warnings)
        self.assertEqual([s.price.status for s in converted.data.skus], ["available", "unavailable"])

    def test_duplicate_roots_and_conflicting_candidates(self):
        first, _, _ = quoted()
        self.assertEqual(parse_detail(page([first, first]), URL).skus[0].price, "39.80")
        for amount in ("39.01", "40.00", "0", True, None):
            other, _, _ = quoted([{"skuId": "901", "price": amount}])
            result = parse_detail(page([first, other]), URL)
            self.assertIsNone(result.skus[0].price)
            self.assertIn("conflicting_sku_price", result.warnings)
            self.assertEqual(len(result.warnings), len(set(result.warnings)))
        absent, _ = trade_page()
        self.assertEqual(parse_detail(page([first, absent]), URL).skus[0].price, "39.80")

    def test_invalid_amount_and_null_keep_sku(self):
        for amount in (True, "0", "NaN", None):
            value, _, _ = quoted([{"skuId": "901", "price": amount}])
            result = parse_detail(page(value), URL)
            self.assertEqual(len(result.skus), 1)
            self.assertIn("sku_price_missing", result.warnings)
            self.assertEqual("invalid_sku_price" in result.warnings, amount is not None)

    def test_old_sources_are_not_money(self):
        value, model = trade_page()
        model["tradeModel"]["skuMap"][0]["priceAmount"] = 3980
        for source in (value, business()):
            result = parse_detail(page(source), URL)
            self.assertTrue(result.ok)
            self.assertIsNone(result.skus[0].price)
            self.assertIn("sku_price_source_missing", result.warnings)
            self.assertIn("sku_price_missing", result.warnings)

    def test_limits_and_result_clearance(self):
        value, _, _ = quoted()
        with patch("product_sku.quote_sources.MAX_NODES", 1):
            result = parse_detail(page([value, value]), URL)
            self.assertEqual((result.reason, result.skus), ("row_limit", []))
            self.assertEqual((result.specification_images, result.sizes, result.color_sizes), ([], [], []))
        with patch("product_sku.quote_sources.MAX_CANDIDATES", 1):
            with self.assertRaises(DecodeLimit):
                collect_quotes([value, value], PID)
        for optional in ({"verified_roots": []}, {"models": []}, {}):
            with patch("product_sku.parser.MAX_NODES", 1):
                with self.assertRaises(DecodeLimit):
                    _arrays([value], PID, **optional)
        candidate, model = trade_page()
        del model["detailDescription"]
        for optional in ({"verified_roots": []}, {"models": []}, {}):
            with patch("product_sku.parser.MAX_CANDIDATES", 1):
                with self.assertRaises(DecodeLimit):
                    _arrays([candidate, candidate], PID, **optional)
        with patch("product_sku.parser.MAX_DEPTH", 1):
            result = parse_detail(page({"payload": value}), URL)
            self.assertEqual((result.reason, result.skus), ("structure_limit", []))

    def test_bad_encoded_container_fails_closed(self):
        value, _, _ = quoted("[{broken]")
        result = parse_detail(page(value), URL)
        self.assertEqual((result.reason, result.skus), ("invalid_nested_json", []))

    def test_diagnostics_share_discovery_and_do_not_emit_secrets(self):
        value, _, _ = quoted()
        value["result"]["data"]["account_secret"] = "synthetic-private-value"
        events = []
        result = inspect(page({"dynamic-private-key": json.dumps(value)}), URL, PID,
                         lambda event, **fields: events.append((event, fields)))
        self.assertEqual(result.skus[0].price, "39.80")
        summary = next(fields for event, fields in events if event == "original_quote_summary")
        self.assertEqual((summary["count"], summary["matched"], summary["amount_types"]), (1, 1, {"str": 1}))
        output = json.dumps(events)
        for forbidden in ("account_secret", "synthetic-private-value", "dynamic-private-key", "39.80", "901"):
            self.assertNotIn(forbidden, output)


if __name__ == "__main__":
    unittest.main()
