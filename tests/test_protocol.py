from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import requests

from image_search.cookies import load_cookie_records, load_cookies
from image_search.mtop import calculate_sign, compact_json, extract_token, parse_json_or_jsonp
from image_search.parser import parse_page


class MtopProtocolTests(unittest.TestCase):
    def test_compact_json_and_sign(self) -> None:
        data = compact_json({"appId": 32517, "params": "{}"})
        self.assertEqual(data, '{"appId":32517,"params":"{}"}')
        self.assertEqual(
            calculate_sign("token", "1700000000000", "12574478", data),
            "96759f855330bcdd9f6b9c54519c4d9b",
        )

    def test_extract_token(self) -> None:
        self.assertEqual(extract_token("abc123_1893456000000"), "abc123")

    def test_parse_jsonp(self) -> None:
        class Response:
            text = 'callback({"ret":["SUCCESS::ok"],"data":{"value":1}})'

            @staticmethod
            def json():
                raise ValueError("not json")

        payload = parse_json_or_jsonp(Response())
        self.assertEqual(payload["data"]["value"], 1)


class CookieImportTests(unittest.TestCase):
    def test_cookie_array_import(self) -> None:
        payload = {
            "cookies": [
                {"name": "cookie2", "value": "login", "domain": ".1688.com", "path": "/"},
                {"name": "_m_h5_tk", "value": "token_1893456000000", "domain": ".1688.com"},
                {"name": "_m_h5_tk_enc", "value": "enc", "domain": ".1688.com"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            records = load_cookie_records(path)
            self.assertEqual(len(records), 3)
            session = requests.Session()
            names = load_cookies(session, path)
            self.assertEqual(names, {"cookie2", "_m_h5_tk", "_m_h5_tk_enc"})

    def test_cookie_string_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.txt"
            path.write_text("cookie2=login; _m_h5_tk=token_1; _m_h5_tk_enc=enc", encoding="utf-8")
            records = load_cookie_records(path)
            self.assertEqual([item.name for item in records], ["cookie2", "_m_h5_tk", "_m_h5_tk_enc"])


class ParserTests(unittest.TestCase):
    def test_parse_confirmed_offer_shape(self) -> None:
        payload = {
            "data": {
                "data": {
                    "OFFER": {
                        "hasMore": "true",
                        "found": "700",
                        "items": [
                            {
                                "data": {
                                    "offerId": "123",
                                    "linkUrl": "https://detail.1688.com/offer/123.html",
                                    "offerPicUrl": "https://img.example/123.jpg",
                                    "title": "测试商品",
                                    "priceInfo": {"price": "12.50"},
                                    "saleQuantity": 88,
                                    "loginId": "seller-login",
                                    "shopAddition": {"text": "测试供应商"},
                                    "isAd": False,
                                }
                            }
                        ],
                    }
                }
            }
        }
        page = parse_page(payload)
        self.assertTrue(page.has_more)
        self.assertEqual(page.found, 700)
        self.assertEqual(len(page.products), 1)
        product = page.products[0]
        self.assertEqual(product.offer_id, "123")
        self.assertEqual(product.price, "12.50")
        self.assertEqual(product.seller_name, "测试供应商")
        self.assertFalse(product.is_ad)

    def test_parse_direct_item_and_ad(self) -> None:
        payload = {
            "data": {
                "OFFER": {
                    "hasMore": False,
                    "items": [{"offerId": 456, "title": "广告", "fmAd": "true", "price": 9.9}],
                }
            }
        }
        page = parse_page(payload)
        self.assertFalse(page.has_more)
        self.assertEqual(page.products[0].offer_id, "456")
        self.assertTrue(page.products[0].is_ad)


if __name__ == "__main__":
    unittest.main()
