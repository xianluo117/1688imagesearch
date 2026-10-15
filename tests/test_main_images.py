"""Main-gallery parsing and API/persisted-result compatibility."""
import copy
import unittest

from test_product_sku import PID, URL, business, model_page, page, trade_page
from product_sku.parser import parse_detail
from api.sku_schemas import ProductSkuResponse
from api.sku_service import restore_result
from api.sku_v2_converter import convert_result

A = "https://img.example/a.jpg"
B = "https://img.example/b.jpg"


def gallery(root, images=None):
    root["result"].setdefault("data", {})["gallery"] = {"fields": {
        "offerId": int(PID), "mainImage": [A, B] if images is None else images,
        "offerImgList": [A, B, "https://img.example/spec.jpg"],
    }}
    return root


class MainImageTests(unittest.TestCase):
    def test_trade_branch_and_order(self):
        root, _ = trade_page()
        result = parse_detail(page(gallery(root, [B, A, B, "//img.example/a.jpg", None])), URL)
        self.assertTrue(result.ok)
        self.assertEqual(result.main_images, [B, A])
        self.assertEqual(result.main_image, B)

    def test_legacy_branch(self):
        result = parse_detail(page(gallery(model_page())), URL)
        self.assertTrue(result.ok)
        self.assertEqual(result.main_images, [A, B])

    def test_meta_preserved_without_fabricating_gallery(self):
        meta = f'<meta property="og:url" content="{URL}"><meta property="og:image" content="{B}">'
        result = parse_detail(page(gallery(model_page()), meta), URL)
        self.assertEqual(result.main_image, B)
        self.assertEqual(result.main_images, [A, B])
        result = parse_detail(page(business(), meta), URL)
        self.assertEqual(result.main_image, B)
        self.assertEqual(result.main_images, [])

    def test_missing_invalid_and_mixed_collection_not_used(self):
        for images in ([], "wrong", [None, 123, "data:image/png,x"]):
            result = parse_detail(page(gallery(model_page(), images)), URL)
            self.assertEqual(result.main_images, [])
            self.assertTrue(result.ok)
        root = gallery(model_page())
        del root["result"]["data"]["gallery"]["fields"]["mainImage"]
        self.assertEqual(parse_detail(page(root), URL).main_images, [])

    def test_ownership_and_recommendations(self):
        for change in ("offerId", "productId", "itemId"):
            root = gallery(model_page())
            root["result"]["data"]["gallery"]["fields"][change] = "123"
            self.assertEqual(parse_detail(page(root), URL).main_images, [])
        root = gallery(model_page())
        root["result"]["global"]["globalData"]["parametersMap"]["offerId"] = "123"
        self.assertEqual(parse_detail(page(root), URL).main_images, [])
        self.assertEqual(parse_detail(page({"recommendations": gallery(model_page())}), URL).main_images, [])

    def test_duplicate_roots(self):
        root = gallery(model_page())
        self.assertEqual(parse_detail(page([root, copy.deepcopy(root)]), URL).main_images, [A, B])

    def test_api_and_persisted_result(self):
        result = parse_detail(page(gallery(model_page())), URL)
        restored = restore_result(result.to_dict())
        self.assertEqual(restored.main_images, [A, B])
        self.assertEqual(ProductSkuResponse.model_validate(restored.to_dict()).main_images, [A, B])
        self.assertEqual(convert_result(restored).data.product.main_images, [A, B])
        old = result.to_dict()
        del old["main_images"]
        self.assertEqual(restore_result(old).main_images, [])
        self.assertEqual(ProductSkuResponse.model_validate(old).main_images, [])
        self.assertEqual(convert_result(restore_result(old)).data.product.main_images, [])


if __name__ == "__main__":
    unittest.main()
