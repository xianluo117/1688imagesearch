"""Synthetic size/price regressions; reviewed monetary contracts are test-only."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from product_sku.models import Sku, Specification
from product_sku.parser import parse_detail
from product_sku.prices import PriceContract, SkuPrices, decimal_price
from product_sku.size_summary import summarize_sizes
from test_product_sku import URL, business, page, trade_page

CONTRACT = PriceContract(2, "CNY", "synthetic_unit_quote")


def sku(number, color, size, color_name="颜色", size_name="尺码"):
    return Sku(str(number), [Specification(1, "sku1", color, color_name),
                             Specification(2, "sku2", size, size_name)])


class SizeSummaryTests(unittest.TestCase):
    def test_dedup_order_raw_values_and_real_links(self):
        sizes, links = summarize_sizes([sku(1, "蓝色", " L "), sku(2, "红色", "M"),
                                        sku(3, "蓝色", "M"), sku(4, "蓝色", " L ")])
        self.assertEqual(sizes[0].values, [" L ", "M"])
        self.assertEqual((sizes[0].position, sizes[0].field, sizes[0].name), (2, "sku2", "尺码"))
        self.assertEqual([(x.color.value, x.values) for x in links], [("蓝色", [" L ", "M"]), ("红色", ["M"])])

    def test_missing_or_vague_name_is_not_inferred(self):
        for name in (None, "规格", "型号", "尺码/型号", "大小规格"):
            self.assertEqual(summarize_sizes([sku(1, "蓝色", "XL", size_name=name)]), ([], []))
        sizes, links = summarize_sizes([sku(1, "蓝色", "M", color_name=None)])
        self.assertEqual(sizes[0].values, ["M"])
        self.assertEqual(links, [])

    def test_inconsistent_names(self):
        self.assertEqual(summarize_sizes([sku(1, "蓝色", "L"), sku(2, "蓝色", "M", size_name=None)]), ([], []))

    def test_multiple_dimensions_never_guess_association(self):
        first = sku(1, "蓝色", "L")
        for extra in (Specification(3, "sku3", "40cm", "尺寸"), Specification(3, "sku3", "黑色", "Color")):
            sizes, links = summarize_sizes([replace(first, specifications=first.specifications + [extra])])
            self.assertEqual(len(sizes), 2 if extra.name == "尺寸" else 1)
            self.assertEqual(links, [])

    def test_order_not_position_and_non_clothing_sizes(self):
        row = Sku("1", [Specification(1, "sku1", "40cm", " SIZE "), Specification(2, "sku2", "银色", "Colour")])
        sizes, links = summarize_sizes([row])
        self.assertEqual(sizes[0].values, ["40cm"])
        self.assertEqual(links[0].color.position, 2)

    def test_legacy_empty_and_failure(self):
        for text in (page(business()), page(business([])), ""):
            result = parse_detail(text, URL)
            self.assertEqual((result.sizes, result.color_sizes), ([], []))
            self.assertTrue(all(row.price is None for row in result.skus))

    def test_final_skus_only(self):
        value, model = trade_page()
        model["tradeModel"]["skuMap"] += [
            {"skuId": "901", "specAttrs": "红色" + chr(38) + "gt;L"},
            {"skuId": "902", "specAttrs": "蓝色" + chr(38) + "gt;M"}]
        result = parse_detail(page(value), URL)
        self.assertEqual(result.sizes[0].values, ["M"])
        self.assertEqual([(x.color.value, x.values) for x in result.color_sizes], [("蓝色", ["M"])])


class PriceTests(unittest.TestCase):
    def test_exact_decimal_and_explicit_units(self):
        self.assertEqual(decimal_price(3980, CONTRACT), "39.80")
        self.assertEqual(decimal_price("123456789012345678901234567890.123456789012", CONTRACT),
                         "1234567890123456789012345678.90123456789012")
        self.assertEqual(decimal_price("39.80", PriceContract(0, "CNY", "test")), "39.80")
        self.assertIsNone(decimal_price(3980, None))

    def test_illegal_amounts_and_contract(self):
        for value in (None, True, False, 39.8, -1, 0, "0", "-1", "1e2", "NaN", "Infinity", " 12 ", "", "1,000", "9" * 41):
            with self.subTest(value=value):
                self.assertIsNone(decimal_price(value, CONTRACT))
        for contract in (PriceContract(-1, "CNY", "test"), PriceContract(2, "?", "test"), PriceContract(2, "CNY", "")):
            self.assertIsNone(decimal_price(100, contract))

    def test_missing_duplicate_conflict_and_invalid_keep_sku(self):
        for amounts, expected in (([100, 100], "1.00"), ([100, None], None), ([100, 200], None), ([None], None), ([True], None)):
            collector = SkuPrices(CONTRACT)
            for amount in amounts:
                collector.add("1", {"priceAmount": amount})
            rows, warnings = collector.finish([sku(1, "蓝色", "L")])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].price, expected)
            self.assertEqual(rows[0].currency, "CNY" if expected else None)
            if len(set(amounts)) > 1:
                self.assertIn("conflicting_sku_price", warnings)

    def test_unknown_production_contract_no_product_fallback(self):
        value, model = trade_page()
        model["tradeModel"].update(currency="CNY", priceUnit="cent", minPrice="39.80")
        model["tradeModel"]["skuMap"][0]["priceAmount"] = 3980
        result = parse_detail(page(value), URL)
        self.assertIsNone(result.skus[0].price)
        self.assertIsNone(result.skus[0].currency)
        self.assertIsNone(result.skus[0].price_source)
        self.assertIsNone(result.skus[0].price_basis)
        self.assertIn("sku_price_unverified", result.warnings)

    def test_validated_row_scope_and_recommendation_isolation(self):
        value, model = trade_page()
        model["tradeModel"]["skuMap"][0]["priceAmount"] = 100
        recommended, other = trade_page()
        other["tradeModel"]["skuMap"][0]["priceAmount"] = 999
        value["recommendations"] = recommended
        model["tradeModel"]["skuMap"].append({"skuId": "901", "specAttrs": "蓝色" + chr(38) + "gt;L", "offerId": "123", "priceAmount": 999})
        with patch("product_sku.parser.SkuPrices", side_effect=lambda: SkuPrices(CONTRACT)):
            result = parse_detail(page(value), URL)
        self.assertEqual(result.skus[0].price, "1.00")

    def test_price_conflict_does_not_change_spec_id_images_or_sku(self):
        value, model = trade_page()
        row = model["tradeModel"]["skuMap"][0]
        row.update(priceAmount=100, specId="a" * 32)
        model["tradeModel"]["skuMap"].append(dict(row, priceAmount=200))
        with patch("product_sku.parser.SkuPrices", side_effect=lambda: SkuPrices(CONTRACT)):
            result = parse_detail(page(value), URL)
        self.assertEqual(len(result.skus), 1)
        self.assertEqual(result.skus[0].spec_id, "a" * 32)
        self.assertIsNone(result.skus[0].price)
        self.assertEqual(len(result.specification_images), 2)
        self.assertIn("conflicting_sku_price", result.warnings)

    def test_multiple_models_missing_price_conflicts(self):
        first, model = trade_page()
        model["tradeModel"]["skuMap"][0]["priceAmount"] = 100
        second, _ = trade_page()
        with patch("product_sku.parser.SkuPrices", side_effect=lambda: SkuPrices(CONTRACT)):
            result = parse_detail(page([first, second]), URL)
        self.assertEqual(len(result.skus), 1)
        self.assertIsNone(result.skus[0].price)
        self.assertIn("conflicting_sku_price", result.warnings)


if __name__ == "__main__":
    unittest.main()
