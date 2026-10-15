"""Focused final-range rules and source-reference regressions."""
from copy import deepcopy
import unittest

from api.sku_v2_converter import convert_result
from product_sku.models import Sku, SkuResult, Specification
from product_sku.size_range import final_size_range
from product_sku.size_summary import summarize_sizes, summarize_sizes_with_sources


def row(number, color, size):
    return Sku(str(number), [Specification(1, "sku1", color, "颜色"),
                             Specification(2, "sku2", size, "尺码")],
               price="39.80", currency="CNY")


class SizeRangeTests(unittest.TestCase):
    def test_sort_shift_and_preserve_gaps(self):
        cases = [
            (["3XL", "L", "XL", "2XL"], ["S", "M", "L", "XL"]),
            (["2XL", "L"], ["S", "L"]),
            (["M", "S", "XL"], ["S", "M", "XL"]),
            (["L", "XS", "S"], ["XS", "S", "L"]),
            (["XXS", "XS", "S"], ["2XS", "XS", "S"]),
            (["10XL", "9XL"], ["S", "M"]),
            ([], []),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(final_size_range(raw)[0], expected)

    def test_standard_aliases_and_exact_nonstandard_intersection(self):
        raw = ["均码", " xxl ", "40cm", "s", "2XL", " XL ", " 40cm"]
        values, sources = final_size_range(raw, [raw, ["S", "XXL", "40cm", "均码"]])
        self.assertEqual(values, ["S", "2XL", "均码", "40cm"])
        self.assertEqual(sources, ["s", " xxl ", "均码", "40cm"])
        self.assertEqual(final_size_range(["40cm"], [[" 40cm"]]), ([], []))

    def test_intersection_precedes_shift(self):
        rows = [row(1, "蓝", "M"), row(2, "蓝", "L"), row(3, "蓝", "2XL"),
                row(4, "红", "L"), row(5, "红", "2XL"), row(6, "红", "3XL")]
        before = deepcopy(rows)
        sizes, colors, sources = summarize_sizes_with_sources(rows)
        self.assertEqual(sizes[0].values, ["S", "L"])
        self.assertEqual(sources, [["L", "2XL"]])
        self.assertEqual([group.values for group in colors], [["M", "L", "2XL"], ["L", "2XL", "3XL"]])
        self.assertEqual(rows, before)

    def test_color_without_valid_size_must_constrain_intersection(self):
        for invalid in (None, "", " "):
            with self.subTest(invalid=invalid):
                sizes, colors = summarize_sizes([row(1, "蓝", "L"), row(2, "红", invalid)])
                self.assertEqual(sizes[0].values, [])
                self.assertEqual([(g.color.value, g.values) for g in colors], [("蓝", ["L"])])
        missing = Sku("2", [Specification(1, "sku1", "红", "颜色")])
        self.assertEqual(summarize_sizes([row(1, "蓝", "L"), missing])[0][0].values, [])

    def test_invalid_row_does_not_erase_same_colors_valid_sizes(self):
        sizes, _ = summarize_sizes([row(1, "蓝", None), row(2, "蓝", "L"), row(3, "红", "L")])
        self.assertEqual(sizes[0].values, ["S"])

    def test_single_color_no_color_and_ambiguous_dimensions(self):
        rows = [row(1, "蓝", "2XL"), row(2, "蓝", "L")]
        self.assertEqual(summarize_sizes(rows)[0][0].values, ["S", "L"])
        no_color = [Sku(s.sku_id, s.specifications[1:]) for s in rows]
        sizes, colors = summarize_sizes(no_color)
        self.assertEqual(sizes[0].values, ["S", "L"])
        self.assertEqual(colors, [])
        rows[1].specifications[0] = Specification(1, "sku1", "红", "颜色")
        for sku in rows:
            sku.specifications.append(Specification(3, "sku3", "黑", "色彩"))
        sizes, colors = summarize_sizes(rows)
        self.assertEqual(sizes[0].values, ["S", "L"])
        self.assertEqual(colors, [])

    def test_v2_mapping_sorted_originals_and_no_external_fields(self):
        rows = [row(1, "蓝", "2XL"), row(2, "蓝", " l "), row(3, "蓝", "XL"),
                row(4, "红", "xxl"), row(5, "红", "L")]
        original = SkuResult("123", "https://detail.1688.com/offer/123.html", "partial_success", "sku_data_found", skus=rows)
        original.sizes, original.color_sizes = summarize_sizes(rows)
        before = original.to_dict()
        payload = convert_result(original)
        dimension = payload.data.size_summary.dimensions[0]
        self.assertEqual(dimension.values, ["S", "L"])
        self.assertEqual(dimension.option_ids, ["d2_o2", "d2_o1"])
        options = payload.data.specifications[1].options
        self.assertEqual([option.value for option in options], ["2XL", " l ", "XL", "xxl", "L"])
        lookup = {o.option_id: o.value for o in options}
        self.assertEqual([lookup[key] for key in dimension.option_ids], [" l ", "2XL"])
        self.assertEqual(payload.data.size_summary.by_color[0].sizes, ["2XL", " l ", "XL"])
        self.assertEqual(payload.data.size_summary.by_color[1].sizes, ["xxl", "L"])
        self.assertEqual([s.price.amount for s in payload.data.skus], ["40"] * 5)
        self.assertEqual(original.to_dict(), before)
        self.assertEqual(set(before["sizes"][0]), {"position", "field", "name", "values"})
        self.assertEqual(set(dimension.model_dump()), {"dimension_id", "name", "values", "option_ids"})


if __name__ == "__main__":
    unittest.main()
