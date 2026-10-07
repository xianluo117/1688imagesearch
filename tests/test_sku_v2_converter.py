"""Offline v2 conversion invariants, including ambiguous and legacy results."""
from dataclasses import replace
import unittest

from api.sku_v2_converter import convert_result
from product_sku.models import Sku, SkuResult, Specification as Spec, SpecificationImage
from product_sku.prices import VERIFIED_CONTRACT

URL = "https://detail.1688.com/offer/123.html"


def result(skus):
    return SkuResult("123", URL, "partial_success", "sku_data_found", skus=skus)


def row(identifier, color="蓝色", size="M"):
    return Sku(identifier, [Spec(1, "sku1", color, "颜色"), Spec(2, "sku2", size, "尺码")])


class ConverterV2Tests(unittest.TestCase):
    def test_dedup_references_and_pure_conversion(self):
        original = result([row("1"), row("2", size="L"), row("3")])
        before = original.to_dict()
        payload = convert_result(original)
        self.assertEqual(original.to_dict(), before)
        dimensions = payload.data.specifications
        self.assertEqual([len(d.options) for d in dimensions], [1, 2])
        ids = {o.option_id for d in dimensions for o in d.options}
        for sku in payload.data.skus:
            self.assertEqual(len(sku.option_ids), 2)
            self.assertTrue(set(sku.option_ids) <= ids)
        self.assertEqual(payload.meta.sku_count, 3)
        self.assertEqual(payload.meta.schema_version, "2")

    def test_real_color_size_combinations_only(self):
        payload = convert_result(result([row("1"), row("2", size="L"), row("3", "红色", "S")]))
        summary = payload.data.size_summary
        self.assertEqual(summary.dimensions[0].values, ["M", "L", "S"])
        self.assertEqual([(g.color, g.sizes) for g in summary.by_color], [("蓝色", ["M", "L"]), ("红色", ["S"])])
        self.assertEqual(summary.by_color[0].size_option_ids, ["d2_o1", "d2_o2"])

    def test_names_not_positions_or_values(self):
        sku = Sku("1", [Spec(1, "sku1", "M", " SIZE "), Spec(2, "sku2", "蓝色", "Colour"), Spec(3, "sku3", "L", "型号")])
        payload = convert_result(result([sku]))
        self.assertEqual([d.role for d in payload.data.specifications], ["size", "color", "unknown"])
        self.assertEqual(payload.data.size_summary.by_color[0].size_dimension_id, "d1")
        self.assertEqual(len(payload.data.skus[0].option_ids), 3)

    def test_multiple_sizes_same_text_do_not_collide(self):
        sku = Sku("1", [Spec(1, "sku1", "M", "颜色"), Spec(2, "sku2", "M", "尺码"), Spec(3, "sku3", "M", "尺寸")])
        payload = convert_result(result([sku]))
        self.assertEqual([d.option_ids for d in payload.data.size_summary.dimensions], [["d2_o1"], ["d3_o1"]])
        self.assertEqual(payload.data.size_summary.by_color, [])
        self.assertEqual(payload.data.skus[0].option_ids, ["d1_o1", "d2_o1", "d3_o1"])

    def test_multiple_colors_no_guessed_pairing(self):
        sku = Sku("1", [Spec(1, "sku1", "蓝", "颜色"), Spec(2, "sku2", "红", "色彩"), Spec(3, "sku3", "M", "尺码")])
        payload = convert_result(result([sku]))
        self.assertEqual(payload.data.size_summary.by_color, [])
        self.assertEqual(len(payload.data.size_summary.dimensions), 1)

    def test_legacy_nullable_structure_and_no_inference(self):
        payload = convert_result(result([Sku("1", [Spec(1, "sku1", "蓝色"), Spec(2, "sku2", None)])]))
        self.assertEqual([d.name for d in payload.data.specifications], [None, None])
        self.assertEqual([d.role for d in payload.data.specifications], ["unknown", "unknown"])
        self.assertIsNone(payload.data.specifications[1].options[0].value)
        self.assertEqual(payload.data.size_summary.dimensions, [])
        self.assertEqual(len(payload.data.skus[0].option_ids), 2)

    def test_missing_and_explicit_name_share_unknown_dimension(self):
        payload = convert_result(result([Sku("1", [Spec(1, "sku1", "蓝", None)]), Sku("2", [Spec(1, "sku1", "红", "颜色")])]))
        self.assertEqual(len(payload.data.specifications), 1)
        dimension = payload.data.specifications[0]
        self.assertIsNone(dimension.name)
        self.assertEqual(dimension.role, "unknown")
        self.assertEqual(len(dimension.options), 2)
        self.assertEqual(payload.data.size_summary.by_color, [])

    def test_conflicting_names_not_forced_into_one_dimension(self):
        payload = convert_result(result([Sku("1", [Spec(1, "sku1", "M", "颜色")]), Sku("2", [Spec(1, "sku1", "M", "尺码")]), Sku("3", [Spec(1, "sku1", "M", None)])]))
        self.assertEqual(len(payload.data.specifications), 3)
        self.assertEqual([d.role for d in payload.data.specifications], ["unknown"] * 3)
        self.assertEqual(len({s.option_ids[0] for s in payload.data.skus}), 3)
        self.assertEqual(payload.data.size_summary.dimensions, [])

    def test_images_use_full_identity_not_order_or_main_image(self):
        original = result([row("1")])
        original.main_image = "https://img.example/main.jpg"
        original.specification_images = [
            SpecificationImage(2, "sku2", "尺码", "M", None),
            SpecificationImage(1, "sku1", "颜色", "unused", "https://img.example/unused.jpg"),
            SpecificationImage(1, "wrong", "颜色", "蓝色", "https://img.example/wrong.jpg"),
            SpecificationImage(1, "sku1", "颜色", "蓝色", "https://img.example/blue.jpg"),
        ]
        payload = convert_result(original)
        self.assertEqual(payload.data.specifications[0].options[0].image_url, "https://img.example/blue.jpg")
        self.assertIsNone(payload.data.specifications[1].options[0].image_url)
        self.assertEqual(len(payload.data.specifications[0].options), 1)

    def test_conflicting_invalid_or_missing_images_remain_null(self):
        for urls in (("https://img.example/a.jpg", "https://img.example/b.jpg"), ("https://img.example/a.jpg", None), ("http://127.0.0.1/a.jpg",)):
            original = result([row("1")])
            original.main_image = "https://img.example/main.jpg"
            original.specification_images = [SpecificationImage(1, "sku1", "颜色", "蓝色", url) for url in urls]
            self.assertIsNone(convert_result(original).data.specifications[0].options[0].image_url)

    def test_name_merge_cannot_borrow_image_from_known_identity(self):
        original = result([Sku("1", [Spec(1, "sku1", "蓝", None)]), Sku("2", [Spec(1, "sku1", "蓝", "颜色")])])
        original.specification_images = [SpecificationImage(1, "sku1", "颜色", "蓝", "https://img.example/a.jpg")]
        payload = convert_result(original)
        self.assertEqual(len(payload.data.specifications[0].options), 1)
        self.assertIsNone(payload.data.specifications[0].options[0].image_url)

    def test_raw_values_preserved_in_summaries(self):
        payload = convert_result(result([row("1", "M", "M"), row("2", "M", " M ")]))
        self.assertEqual(payload.data.size_summary.dimensions[0].values, ["M", " M "])
        self.assertEqual(payload.data.size_summary.by_color[0].color_option_id, "d1_o1")
        self.assertEqual(payload.data.size_summary.by_color[0].size_option_ids, ["d2_o1", "d2_o2"])

    def test_price_null_and_global_warning_not_per_sku_diagnosis(self):
        self.assertEqual(VERIFIED_CONTRACT.currency, "CNY")
        self.assertEqual(VERIFIED_CONTRACT.basis, "detail_html_sku_original_quote_without_promotion")
        original = result([row("1"), row("2")])
        original.warnings = ["sku_price_unverified", "invalid_sku_price", "conflicting_sku_price"]
        payload = convert_result(original)
        for sku in payload.data.skus:
            self.assertEqual(sku.price.model_dump(), {"amount": None, "currency": None, "status": "unavailable", "source": None, "basis": None})
        self.assertEqual(payload.meta.warnings, original.warnings)

    def test_synthetic_valid_price_and_unusable_values(self):
        synthetic = replace(row("1"), price="39.80", currency="CNY", price_source="synthetic", price_basis="test_only")
        price = convert_result(result([synthetic])).data.skus[0].price
        self.assertEqual(price.status, "available")
        self.assertEqual(price.amount, "40")
        for changes in ({"price": "0"}, {"price": "NaN"}, {"price": "-1"}, {"price": "1e2"}):
            price = convert_result(result([replace(synthetic, **changes)])).data.skus[0].price
            self.assertEqual(price.status, "unavailable")
            self.assertIsNone(price.amount)
            self.assertIsNone(price.currency)

    def test_price_availability_ignores_metadata(self):
        for metadata in (
            {},
            {"currency": "CNY"},
            {"currency": "", "price_source": "", "price_basis": ""},
            {"currency": "CNY", "price_source": "discount", "price_basis": "detail_html_sku_discount_quote"},
            {"currency": "CNY", "price_source": "original", "price_basis": "detail_html_sku_original_quote_without_promotion"},
        ):
            with self.subTest(metadata=metadata):
                sku = replace(row("1"), price="28.00", **metadata)
                price = convert_result(result([sku])).data.skus[0].price
                self.assertEqual(price.amount, "28")
                self.assertEqual(price.status, "available")
                self.assertEqual(set(price.model_dump()), {"amount", "currency", "status", "source", "basis"})
                self.assertEqual(sku.price, "28.00")  # Internal exact amount stays unchanged.

    def test_empty_result_and_seller_spec_ids(self):
        original = result([])
        original.status = "source_not_applicable"
        payload = convert_result(original)
        self.assertEqual(payload.data.specifications, [])
        self.assertEqual(payload.data.size_summary.by_color, [])
        self.assertEqual(payload.meta.sku_count, 0)
        original.skus = [replace(row("1"), spec_id="AbCd")]
        original.seller_user_id = "987654321012345678"
        original.seller_member_id = "Member_01"
        payload = convert_result(original)
        self.assertEqual(payload.data.skus[0].spec_id, "AbCd")
        self.assertEqual(payload.data.seller.user_id, original.seller_user_id)
        self.assertEqual(payload.data.seller.member_id, "Member_01")


if __name__ == "__main__":
    unittest.main()
