"""Synthetic specId tests, with no real identifiers or network."""
import copy
import unittest

from test_product_sku import URL, page, trade_page, business
from product_sku.parser import parse_detail

SPEC = "0123456789abcdef0123456789ABCDEF"
OTHER = "abcdef0123456789abcdef0123456789"


def fixture():
    value, model = trade_page()
    row = model["tradeModel"]["skuMap"][0]
    row["specId"] = SPEC
    return value, model, row


class SpecIdTests(unittest.TestCase):
    def test_original_value_and_sku_preserved(self):
        value, _, _ = fixture()
        result = parse_detail(page(value), URL)
        self.assertEqual(result.skus[0].sku_id, "901")
        self.assertEqual(result.skus[0].spec_id, SPEC)
        self.assertEqual(result.to_dict()["skus"][0]["spec_id"], SPEC)

    def test_missing_invalid_does_not_drop_sku(self):
        for raw in (None, "", 123, True, "x" * 32, "a" * 31, " " + SPEC, SPEC + "\n"):
            value, _, row = fixture()
            row["specId"] = raw
            result = parse_detail(page(value), URL)
            self.assertTrue(result.ok)
            self.assertIsNone(result.skus[0].spec_id)
            if raw is not None:
                self.assertIn("invalid_spec_id", result.warnings)

    def test_duplicate_same_value(self):
        value, model, row = fixture()
        model["tradeModel"]["skuMap"].append(copy.deepcopy(row))
        result = parse_detail(page(value), URL)
        self.assertEqual(len(result.skus), 1)
        self.assertEqual(result.skus[0].spec_id, SPEC)

    def test_same_sku_conflicting_or_missing_spec_id(self):
        for other in (OTHER, None):
            for reverse in (False, True):
                value, model, row = fixture()
                rows = [row, dict(row, specId=other)]
                model["tradeModel"]["skuMap"] = rows[::-1] if reverse else rows
                result = parse_detail(page(value), URL)
                self.assertEqual(len(result.skus), 1)
                self.assertIsNone(result.skus[0].spec_id)
                self.assertIn("conflicting_spec_id", result.warnings)

    def test_shared_spec_id_across_skus_is_null(self):
        value, model, row = fixture()
        model["tradeModel"]["skuMap"].append(dict(row, skuId="902", specAttrs="蓝色" + chr(38) + "gt;M"))
        result = parse_detail(page(value), URL)
        self.assertEqual(len(result.skus), 2)
        self.assertTrue(all(s.spec_id is None for s in result.skus))

    def test_bad_mapping_and_recommendation_cannot_supply_id(self):
        value, _, row = fixture()
        recommended = copy.deepcopy(value)
        row.pop("specId")
        value["recommendations"] = recommended
        result = parse_detail(page(value), URL)
        self.assertIsNone(result.skus[0].spec_id)
        row["specId"] = SPEC
        row["specAttrs"] = "invalid"
        self.assertFalse(parse_detail(page(value), URL).ok)

    def test_legacy_defaults_null(self):
        result = parse_detail(page(business()), URL)
        self.assertTrue(result.ok)
        self.assertIsNone(result.skus[0].spec_id)

    def test_outer_damage_never_salvaged(self):
        value, _, _ = fixture()
        import json
        raw = json.dumps(value)[:-1] + ',"broken":[}'
        self.assertEqual(parse_detail('<script>' + raw + '</script>', URL).skus, [])
