"""Offline price priority regressions; IDs/specs synthetic, observed amounts retained."""
import copy
import unittest

from test_sku_quote_sources import quoted
from test_product_sku import PID, URL, page, trade_page
from product_sku.parser import parse_detail
from product_sku.price_diagnostics import inspect
from product_sku.prices import DISCOUNT_SOURCE
from api.sku_schemas import ProductSkuResponse
from api.sku_v2_converter import convert_result


def target_shape():
    value, model, container = quoted([])
    colors = [f"color{i}" for i in range(9)]
    sizes = [f"size{i}" for i in range(6)]
    props = model["offerDetail"]["skuProps"]
    props[0]["value"] = [{"name": c} for c in colors]
    props[1]["value"] = [{"name": s} for s in sizes]
    rows = []
    for index, (color, size) in enumerate((c, s) for c in colors for s in sizes):
        rows.append({"skuId": str(9000 + index), "specAttrs": color + chr(38) + "gt;" + size,
                     "discountPrice": "28.00", "promotionSku": False,
                     "priceAmount": 123456})  # Synthetic non-money integer, not real evidence.
    model["tradeModel"].update(skuMap=rows, minPrice="28.00", maxPrice="28.00", unit="件")
    container["skuMapOriginal"] = [{"skuId": row["skuId"], "priceAmount": 123456} for row in rows]
    value["result"]["data"]["mainPrice"]["fields"].update(originPriceType="rangePrice", unit="件")
    return value, model, container


class DiscountPriorityTests(unittest.TestCase):
    def test_observed_54_sku_shape(self):
        value, _, _ = target_shape()
        events = []
        result = inspect(page(value), URL, PID, lambda e, **f: events.append((e, f)), samples=True)
        self.assertEqual(len(result.skus), 54)
        self.assertEqual({s.price for s in result.skus}, {"28.00"})
        self.assertEqual(result.warnings, ["sku_completeness_unknown"])
        self.assertEqual({s.price_source for s in result.skus}, {DISCOUNT_SOURCE})
        self.assertEqual(len(ProductSkuResponse(**result.to_dict()).skus), 54)
        converted = convert_result(result)
        self.assertEqual({s.price.status for s in converted.data.skus}, {"available"})
        self.assertEqual({s.price.amount for s in converted.data.skus}, {"28"})
        self.assertEqual({s["price"] for s in ProductSkuResponse(
            **result.to_dict()).model_dump(mode="json")["skus"]}, {"28"})
        original = next(f for e, f in events if e == "fixed_quote_container"
                        and f["path"].endswith("tradeWithoutPromotion.skuMapOriginal"))
        self.assertEqual(original["fields"]["price"]["missing"], 54)
        self.assertEqual(original["matched"], 54)

    def test_original_wins(self):
        value, model, _ = quoted()
        model["tradeModel"]["skuMap"][0]["discountPrice"] = "28.00"
        self.assertEqual(parse_detail(page(value), URL).skus[0].price, "39.80")

    def test_unusable_original_falls_back(self):
        for raw in (None, "NaN", "0", True):
            value, model, _ = quoted([{"skuId": "901", "price": raw}])
            model["tradeModel"]["skuMap"][0]["discountPrice"] = "28.00"
            result = parse_detail(page(value), URL)
            self.assertEqual(result.skus[0].price, "28.00")
            self.assertEqual(result.warnings, ["sku_completeness_unknown"])

    def test_discount_only_no_missing_warning(self):
        value, model = trade_page()
        model["tradeModel"]["skuMap"][0]["discountPrice"] = "28.00"
        self.assertEqual(parse_detail(page(value), URL).warnings, ["sku_completeness_unknown"])

    def test_discount_conflicts_fail_closed(self):
        first, model = trade_page()
        model["tradeModel"]["skuMap"][0]["discountPrice"] = "28.00"
        second = copy.deepcopy(first)
        second["result"]["global"]["globalData"]["model"]["tradeModel"]["skuMap"][0]["discountPrice"] = "29.00"
        result = parse_detail(page([first, second]), URL)
        self.assertIsNone(result.skus[0].price)
        self.assertIn("conflicting_sku_price", result.warnings)

    def test_ownership_and_spec_conflicts(self):
        value, model, _ = target_shape()
        model["tradeModel"]["skuMap"][0]["offerId"] = "123"
        result = parse_detail(page(value), URL)
        self.assertNotIn("9000", {s.sku_id for s in result.skus})
        value, model, _ = target_shape()
        row = dict(model["tradeModel"]["skuMap"][0], specAttrs="color1" + chr(38) + "gt;size0")
        model["tradeModel"]["skuMap"].append(row)
        self.assertNotIn("9000", {s.sku_id for s in parse_detail(page(value), URL).skus})

    def test_no_minimum_or_amount_fallback(self):
        value, model, _ = target_shape()
        for row in model["tradeModel"]["skuMap"]:
            del row["discountPrice"]
        self.assertTrue(all(s.price is None for s in parse_detail(page(value), URL).skus))


if __name__ == "__main__":
    unittest.main()
