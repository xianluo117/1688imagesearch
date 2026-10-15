"""Offline title parsing, v2 presentation and persisted-result compatibility."""
import unittest

from test_product_sku import PID, URL, business, model_page, page, trade_page
from product_sku.parser import parse_detail
from api.sku_schemas import ProductSkuResponse
from api.sku_service import restore_result
from api.sku_v2_converter import convert_result


class ProductTitleTests(unittest.TestCase):
    def test_model_title_in_both_sku_branches(self):
        for root in (model_page(), trade_page()[0]):
            for key in ("subject", "title", "offerTitle"):
                detail = root["result"]["global"]["globalData"]["model"]["offerDetail"]
                detail[key] = "  中文商品标题  "
                result = parse_detail(page(root), URL)
                self.assertTrue(result.ok)
                self.assertEqual(result.title, "中文商品标题")
                del detail[key]

    def test_offer_base_info(self):
        root = model_page()
        root["result"]["data"] = {"Root": {"fields": {"dataJson": {
            "offerBaseInfo": {"offerId": PID, "subject": "商品名称"},
        }}}}
        self.assertEqual(parse_detail(page(root), URL).title, "商品名称")

    def test_metadata_fallback(self):
        meta = f'<meta property="og:url" content="{URL}"><meta property="og:title" content="中文 & 标题">'
        self.assertEqual(parse_detail(page(business(), meta), URL).title, "中文 & 标题")
        self.assertIsNone(parse_detail(page(business(), meta.replace(PID, "123")), URL).title)
        self.assertIsNone(parse_detail(page(business(), '<title>网页标题</title>'), URL).title)

    def test_structured_title_precedes_metadata(self):
        root = model_page()
        root["result"]["global"]["globalData"]["model"]["offerDetail"]["subject"] = "商品标题"
        meta = f'<meta property="og:url" content="{URL}"><meta property="og:title" content="网页标题">'
        self.assertEqual(parse_detail(page(root, meta), URL).title, "商品标题")

    def test_missing_blank_invalid_and_conflicting_titles(self):
        for title in (None, "", "  ", 123, {"title": "嵌套标题"}):
            root = model_page()
            root["result"]["global"]["globalData"]["model"]["offerDetail"]["subject"] = title
            result = parse_detail(page(root), URL)
            self.assertTrue(result.ok)
            self.assertIsNone(result.title)
        root["result"]["global"]["globalData"]["model"]["offerDetail"].update(subject="甲", title="乙")
        self.assertIsNone(parse_detail(page(root), URL).title)

    def test_unrelated_titles_are_not_used(self):
        root = model_page()
        detail = root["result"]["global"]["globalData"]["model"]["offerDetail"]
        detail["subject"] = "其他商品"
        self.assertIsNone(parse_detail(page({"recommendations": root}), URL).title)
        detail["offerId"] = "123"
        self.assertIsNone(parse_detail(page(root), URL).title)

    def test_v2_persistence_and_v1_unchanged(self):
        root = model_page()
        root["result"]["global"]["globalData"]["model"]["offerDetail"]["subject"] = "中文商品"
        result = parse_detail(page(root), URL)
        restored = restore_result(result.to_dict())
        self.assertEqual(convert_result(restored).model_dump()["data"]["product"]["title"], "中文商品")
        self.assertNotIn("title", ProductSkuResponse.model_validate(restored.to_dict()).model_dump())
        old = result.to_dict()
        del old["title"]
        self.assertIsNone(convert_result(restore_result(old)).data.product.title)


if __name__ == "__main__":
    unittest.main()
