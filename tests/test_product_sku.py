"""Synthetic offline tests only. No real credentials, pages or network requests."""
import io
import json
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from curl_cffi import requests
from image_search.cookies import ImportedCookie
from product_sku.client import ProductSkuClient
from product_sku.extraction import MAX_BYTES
from product_sku.parser import parse_detail
from product_sku.session import detail_cookie_jar
from product_sku.urls import normalize_url, redirect_target
from fetch_1688_sku import main

URL = "https://detail.1688.com/offer/772946834568.html"
PID = "772946834568"
ROW = {"skuId": "900000000000000001", "sku1": "蓝色", "sku3": "L"}
COOKIE = ImportedCookie("cookie2", "synthetic", expires=4102444800)


def page(value, prefix=""):
    return prefix + '<script type="application/json">' + json.dumps(value, ensure_ascii=False) + '</script>'


def business(rows=None):
    return {"offerId": PID, "pieceWeightScaleInfo": [ROW] if rows is None else rows}


class ParserTests(unittest.TestCase):
    def test_basic_positions_and_string_ids(self):
        result = parse_detail(page(business()), URL)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.to_dict()["sku_count"], 1)
        self.assertEqual(result.skus[0].sku_id, ROW["skuId"])
        self.assertEqual([s.value for s in result.skus[0].specifications], ["蓝色", None, "L"])
        self.assertTrue(all(s.name is None for s in result.skus[0].specifications))

    def test_nested_strings_and_escaping(self):
        row = {"skuId": 123, "sku1": '引号" 反斜杠\\ 换行\n &', "sku2": ""}
        value = json.dumps({"payload": json.dumps(business([row]))})
        result = parse_detail(page(value), URL)
        self.assertEqual(result.skus[0].specifications[0].value, row["sku1"])
        self.assertEqual(result.skus[0].specifications[1].value, "")

    def test_assignment(self):
        html = '<script>window.DATA = ' + json.dumps(business()) + ';</script>'
        self.assertTrue(parse_detail(html, URL).ok)

    def test_invalid_ids_and_specs(self):
        rows = [{"skuId": x, "sku1": "x"} for x in (True, 1.2, None, "1.2", "", -1)]
        rows += [{"skuId": 2, "sku1": ""}, {"skuId": 3, "sku1": True}, {"skuId": 4, "sku99": "x"}]
        self.assertEqual(parse_detail(page(business(rows)), URL).reason, "no_valid_skus")

    def test_duplicate_and_conflict_removed(self):
        self.assertEqual(len(parse_detail(page(business([ROW, ROW])), URL).skus), 1)
        conflict = dict(ROW, sku1="红色")
        result = parse_detail(page(business([ROW, conflict, ROW, dict(ROW, skuId="2")])), URL)
        self.assertEqual([s.sku_id for s in result.skus], ["2"])
        self.assertIn("conflicting_sku_rows", result.warnings)

    def test_other_products_and_recommendations(self):
        value = business()
        value["recommendations"] = business([dict(ROW, skuId="2")])
        value["other"] = {"offerId": "123", "pieceWeightScaleInfo": [dict(ROW, skuId="3")]}
        self.assertEqual(len(parse_detail(page(value), URL).skus), 1)
        self.assertEqual(parse_detail(page(value["other"]), URL).reason, "field_missing")

    def test_unscoped_and_conflicting_identity(self):
        for value in ({"pieceWeightScaleInfo": [ROW]}, dict(business(), productId="123")):
            self.assertEqual(parse_detail(page(value), URL).reason, "unconfirmed_product_context")

    def test_missing_empty_malformed(self):
        cases = [(page({"offerId": PID}), "field_missing"),
                 (page(business([])), "empty_array"),
                 (page(business({})), "invalid_field_format"),
                 ('<script>{"offerId":"' + PID + '","pieceWeightScaleInfo":[}</script>', "invalid_json")]
        for html, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(parse_detail(html, URL).reason, expected)

    def test_challenge_and_normal_login_text(self):
        html = '<script src="/_____tmd_____/punish?secret=hidden"></script>'
        self.assertEqual(parse_detail(html, URL).status, "access_restricted")
        self.assertTrue(parse_detail(page(business(), "请登录 验证码"), URL).ok)
        self.assertEqual(parse_detail('<form action="https://login.1688.com/member/signin"></form>', URL).status, "login_required")

    def test_main_image_is_bound_to_current_product(self):
        meta = '<meta property="og:url" content="{}"><meta property="og:image" content="https://img.example/a.jpg?x=1&y=2">'
        self.assertEqual(parse_detail(page(business(), meta.format(URL)), URL).main_image,
                         "https://img.example/a.jpg?x=1&y=2")
        self.assertIsNone(parse_detail(page(business(), meta.format(URL.replace(PID, "123"))), URL).main_image)
        self.assertIsNone(parse_detail(page(dict(business(), mainImage="https://img.example/unverified.jpg")), URL).main_image)

    def test_limits(self):
        self.assertEqual(parse_detail("x" * (MAX_BYTES + 1), URL).reason, "response_too_large")
        value = business()
        for _ in range(40):
            value = {"nested": value}
        self.assertEqual(parse_detail(page(value), URL).reason, "structure_limit")
        self.assertEqual(parse_detail('<script>' + '{};' * 257 + '</script>', URL).reason, "candidate_limit")

    def test_duplicate_keys_rejected(self):
        raw = json.dumps(business())
        raw = raw.replace('"offerId":', '"offerId": "123", "offerId":')
        result = parse_detail('<script>' + raw + '</script>', URL)
        self.assertEqual(result.reason, "invalid_json")
        self.assertFalse(result.ok)

    def test_unknown_list_and_entity_do_not_inherit_owner(self):
        for child in ([{"pieceWeightScaleInfo": [ROW]}],
                      {"id": "123", "pieceWeightScaleInfo": [ROW]}):
            result = parse_detail(page({"offerId": PID, "other": child}), URL)
            self.assertEqual(result.reason, "unconfirmed_product_context")

    def test_encoded_array_and_empty_array(self):
        self.assertTrue(parse_detail(page(business(json.dumps([ROW]))), URL).ok)
        self.assertEqual(parse_detail(page(business("[]")), URL).reason, "empty_array")

    def test_no_execution_or_inner_salvage(self):
        html = '<script>bad(' + "'" + json.dumps(business()) + "'" + ')</script>'
        self.assertFalse(parse_detail(html, URL).ok)
        html = '<script>{bad:' + json.dumps(business()) + '}</script>'
        self.assertFalse(parse_detail(html, URL).ok)


def model_page():
    return {"result": {"global": {"globalData": {
        "parametersMap": {"offerId": PID},
        "model": {"offerDetail": {"offerId": PID}, "tradeModel": {"offerId": PID},
                  "detailDescription": {"pieceWeightScale": {"pieceWeightScaleInfo": [ROW]}}},
    }}}}


class ObservedStructureTests(unittest.TestCase):
    def test_function_wrapper_and_numeric_keys(self):
        raw = json.dumps(model_page())
        raw = raw[:-1] + ', "module": {12: {"name": "synthetic"}}}'
        wrapper = '(function(a,b){var c={}; for(var k in a){if(a[k]){c[k]=a[k]}} return c})(window.seed,'
        result = parse_detail('<script>window.DATA=' + wrapper + raw + ');</script>', URL)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.skus), 1)
        self.assertEqual([s.position for s in result.skus[0].specifications], [1, 2, 3])

    def test_complete_object_required(self):
        for suffix in (',"broken":[}', ',"broken":undefined}', ',"broken":run()}', ',"module":{12:1,"12":2}}'):
            raw = json.dumps(model_page())[:-1] + suffix
            self.assertFalse(parse_detail('<script>' + raw + '</script>', URL).ok)

    def test_all_three_identities_required(self):
        for name in ("parametersMap", "offerDetail", "tradeModel"):
            for invalid in (None, "123", True):
                value = model_page()
                glob = value["result"]["global"]["globalData"]
                source = glob[name] if name == "parametersMap" else glob["model"][name]
                source["offerId"] = invalid
                self.assertFalse(parse_detail(page(value), URL).ok)

    def test_conflict_and_recommendation_exclusion(self):
        value = model_page()
        value["result"]["global"]["globalData"]["model"]["offerDetail"]["productId"] = "123"
        self.assertFalse(parse_detail(page(value), URL).ok)
        self.assertFalse(parse_detail(page({"recommendations": model_page()}), URL).ok)

    def test_no_generic_sibling_inheritance(self):
        value = {"offerDetail": {"offerId": PID}, "detailDescription": {"pieceWeightScaleInfo": [ROW]}}
        self.assertFalse(parse_detail(page(value), URL).ok)

    def test_function_body_is_opaque(self):
        html = '<script>function f(){var hidden=' + json.dumps(business()) + ';}</script>'
        self.assertFalse(parse_detail(html, URL).ok)
        html = '<script>{bad:function(){return 1},"child":' + json.dumps(business()) + '}</script>'
        self.assertFalse(parse_detail(html, URL).ok)

    def test_wrapper_comments_and_strings(self):
        wrapper = 'function f(){/* } */ var x="}"; // }\\n return {x:1};} '
        self.assertTrue(parse_detail('<script>' + wrapper.replace('\\n', '\n') + json.dumps(business()) + '</script>', URL).ok)

    def test_numeric_key_limits_and_duplicates(self):
        for keys in ('01:0', '9007199254740992:0', '1:0,"1":1'):
            raw = json.dumps(business())[:-1] + ',"module":{' + keys + '}}'
            self.assertFalse(parse_detail('<script>' + raw + '</script>', URL).ok)


def trade_page():
    value = model_page()
    model = value["result"]["global"]["globalData"]["model"]
    model["detailDescription"]["pieceWeightScale"]["pieceWeightScaleInfo"] = [{"weight": 1}]
    model["offerDetail"]["skuProps"] = [
        {"fid": 1, "prop": "颜色", "value": [{"name": "蓝色"}, {"name": "红色"}]},
        {"fid": 2, "prop": "尺寸", "value": [{"name": "L"}, {"name": "M"}]},
    ]
    model["tradeModel"]["skuMap"] = [{"skuId": "901", "specAttrs": "蓝色" + chr(38) + "gt;L"}]
    return value, model


class TradeModelTests(unittest.TestCase):
    def test_named_positions_no_images_no_cartesian_generation(self):
        value, _ = trade_page()
        result = parse_detail(page(value), URL)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.skus), 1)
        self.assertEqual(result.skus[0].sku_id, "901")
        self.assertEqual([(s.position, s.name, s.value) for s in result.skus[0].specifications],
                         [(1, "颜色", "蓝色"), (2, "尺寸", "L")])
        self.assertIsNone(result.main_image)
        self.assertEqual(result.warnings, ["sku_completeness_unknown"])

    def test_bad_mapping_is_not_guessed(self):
        for attrs in ("L" + chr(38) + "gt;蓝色", "蓝色>L", "蓝色", "绿色" + chr(38) + "gt;L"):
            value, model = trade_page()
            model["tradeModel"]["skuMap"][0]["specAttrs"] = attrs
            self.assertFalse(parse_detail(page(value), URL).ok)

    def test_missing_conflicting_identities_and_recommendations(self):
        for name in ("offerDetail", "tradeModel"):
            value, model = trade_page()
            model[name]["offerId"] = "123"
            self.assertFalse(parse_detail(page(value), URL).ok)
        value, _ = trade_page()
        self.assertFalse(parse_detail(page({"related": value}), URL).ok)
        value["offerId"] = "123"
        self.assertFalse(parse_detail(page(value), URL).ok)

    def test_duplicate_names_values_and_separator_ambiguity(self):
        for mode in ("name", "value", "delimiter"):
            value, model = trade_page()
            props = model["offerDetail"]["skuProps"]
            if mode == "name":
                props[1]["prop"] = props[0]["prop"]
            elif mode == "value":
                props[0]["value"].append(props[0]["value"][0])
            else:
                props[0]["value"][0]["name"] += chr(38) + "gt;"
            self.assertFalse(parse_detail(page(value), URL).ok)

    def test_conflicting_rows_removed(self):
        value, model = trade_page()
        model["tradeModel"]["skuMap"] += [
            {"skuId": "901", "specAttrs": "红色" + chr(38) + "gt;L"},
            {"skuId": "902", "specAttrs": "蓝色" + chr(38) + "gt;M"}]
        result = parse_detail(page(value), URL)
        self.assertEqual([s.sku_id for s in result.skus], ["902"])
        self.assertIn("conflicting_sku_rows", result.warnings)

    def test_broken_outer_trade_data_not_salvaged(self):
        value, _ = trade_page()
        raw = json.dumps(value)[:-1] + ',"broken":[}'
        self.assertFalse(parse_detail('<script>' + raw + '</script>', URL).ok)


def seller_page():
    value, model = trade_page()
    base = {"offerId": PID, "sellerUserId": 987654321012345678,
            "sellerMemberId": "synthetic_member-01", "buyerUserId": 123,
            "buyerMemberId": "synthetic_buyer", "sellerLoginId": "not-returned"}
    value["result"]["data"] = {"Root": {"fields": {"dataJson": {"offerBaseInfo": base}}}}
    return value, base


class SellerTests(unittest.TestCase):
    def test_two_namespaces_as_strings(self):
        value, _ = seller_page()
        result = parse_detail(page(value), URL)
        self.assertTrue(result.ok)
        self.assertEqual(result.seller_user_id, "987654321012345678")
        self.assertEqual(result.seller_member_id, "synthetic_member-01")
        self.assertNotIn("synthetic_buyer", json.dumps(result.to_dict()))
        self.assertNotIn("not-returned", json.dumps(result.to_dict()))

    def test_no_buyer_or_login_fallback(self):
        value, base = seller_page()
        del base["sellerUserId"]
        del base["sellerMemberId"]
        result = parse_detail(page(value), URL)
        self.assertTrue(result.ok)
        self.assertIsNone(result.seller_user_id)
        self.assertIsNone(result.seller_member_id)

    def test_mismatch_missing_identity_and_recommendation(self):
        for mode in ("mismatch", "missing", "conflict", "ancestor", "recommendation"):
            value, base = seller_page()
            if mode == "mismatch":
                base["offerId"] = "123"
            elif mode == "missing":
                del base["offerId"]
            elif mode == "conflict":
                base["productId"] = "123"
            elif mode == "ancestor":
                value["result"]["data"]["offerId"] = "123"
            else:
                plain, _ = trade_page()
                plain["recommendations"] = value
                value = plain
            result = parse_detail(page(value), URL)
            self.assertTrue(result.ok)
            self.assertIsNone(result.seller_user_id)
            self.assertIsNone(result.seller_member_id)

    def test_invalid_user_id_keeps_valid_member(self):
        for invalid in (True, 1.2, -1, "0", "1e5", " secret "):
            value, base = seller_page()
            base["sellerUserId"] = invalid
            result = parse_detail(page(value), URL)
            self.assertIsNone(result.seller_user_id)
            self.assertEqual(result.seller_member_id, "synthetic_member-01")
            self.assertIn("seller_user_id_unverified", result.warnings)

    def test_invalid_member_id_keeps_valid_user(self):
        for invalid in (True, 123, "", "x\n", "a b", "https://example.com", "x" * 129):
            value, base = seller_page()
            base["sellerMemberId"] = invalid
            result = parse_detail(page(value), URL)
            self.assertIsNone(result.seller_member_id)
            self.assertEqual(result.seller_user_id, "987654321012345678")

    def test_conflicting_candidates_do_not_pick_first(self):
        first, _ = seller_page()
        second, base = seller_page()
        base["sellerUserId"] = "222"
        for values in ([first, second], [second, first]):
            result = parse_detail(page(values), URL)
            self.assertIsNone(result.seller_user_id)
            self.assertEqual(result.seller_member_id, "synthetic_member-01")
            self.assertIn("seller_user_id_unverified", result.warnings)
        base["sellerMemberId"] = "different_member"
        result = parse_detail(page([first, second]), URL)
        self.assertIsNone(result.seller_member_id)

    def test_failure_and_broken_outer_never_expose_seller(self):
        value, _ = seller_page()
        raw = json.dumps(value)[:-1] + ',"broken":[}'
        result = parse_detail('<script>' + raw + '</script>', URL)
        self.assertFalse(result.ok)
        self.assertIsNone(result.seller_user_id)
        self.assertIsNone(result.seller_member_id)


class UrlCookieTests(unittest.TestCase):
    def test_url_normalization(self):
        self.assertEqual(normalize_url(URL.replace("https", "http") + "?trace=secret#x"), (PID, URL))
        for url in (URL.replace(".com", ".com.evil"), URL.replace("https://", "https://u@"),
                    URL.replace(".com/", ".com:443/"), URL + "\n", URL.replace("https", "ftp"),
                    URL.replace("/offer/", "/x/"), URL.replace(".html", ".html/")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)

    def test_redirect_policy(self):
        self.assertEqual(redirect_target(URL, "/offer/" + PID + ".html?x=1", PID), (URL, None))
        for target in ("https://evil.example/", URL.replace(PID, "123"), URL.replace("https", "http")):
            self.assertEqual(redirect_target(URL, target, PID)[1], "access_restricted")
        self.assertEqual(redirect_target(URL, "https://login.1688.com/?secret=x", PID)[1], "login_required")

    def test_real_curl_cookiejar_preserves_attributes(self):
        records = [COOKIE, ImportedCookie("expired", "x", expires=1),
                   ImportedCookie("foreign", "x", domain=".evil1688.com"),
                   ImportedCookie("subdomain", "x", domain="other.1688.com"),
                   ImportedCookie("scoped", "x", domain="detail.1688.com", path="/offer", secure=False)]
        jar = detail_cookie_jar(records)
        with requests.Session(impersonate="chrome", cookies=jar) as session:
            cookies = {c.name: c for c in session.cookies.jar}
            self.assertEqual(set(cookies), {"cookie2", "scoped"})
            self.assertEqual(cookies["cookie2"].expires, 4102444800)
            self.assertTrue(cookies["cookie2"].secure)
            self.assertEqual(cookies["scoped"].path, "/offer")
            self.assertFalse(cookies["scoped"].secure)

    def test_no_usable_cookies(self):
        with self.assertRaises(ValueError):
            detail_cookie_jar([ImportedCookie("old", "x", expires=1)])


class FakeResponse:
    def __init__(self, body=None, code=200, headers=None, error=False):
        self.status_code = code
        self.headers = headers or {}
        self.url = URL
        self.body = page(business()).encode() if body is None else body
        self.closed = False
        self.error = error

    def iter_content(self, chunk_size):
        yield self.body[:10]
        if self.error:
            raise requests.RequestsError("SENSITIVE")
        yield self.body[10:]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class ClientTests(unittest.TestCase):
    def fetch(self, responses, **kwargs):
        session = FakeSession(responses)
        with ProductSkuClient(cookie_records=[COOKIE], session=session, **kwargs) as client:
            result = client.fetch(URL)
        self.assertFalse(session.closed)
        return result, session

    def test_stream_and_borrowed_session(self):
        response = FakeResponse()
        result, session = self.fetch([response])
        self.assertTrue(result.ok)
        self.assertTrue(response.closed)
        options = session.calls[0][1]
        self.assertTrue(options["stream"])
        self.assertFalse(options["allow_redirects"])
        self.assertNotIn("Origin", options["headers"])
        self.assertNotIn("Referer", options["headers"])

    def test_owned_session_closed(self):
        session = FakeSession([FakeResponse()])
        with patch("product_sku.client.requests.Session", return_value=session):
            with ProductSkuClient(cookie_records=[COOKIE]) as client:
                self.assertTrue(client.fetch(URL).ok)
        self.assertTrue(session.closed)

    def test_restricted_status_no_retry(self):
        for code, status in ((429, "access_restricted"), (403, "access_restricted"), (401, "login_required")):
            response = FakeResponse(code=code)
            result, session = self.fetch([response])
            self.assertEqual(result.status, status)
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(response.closed)

    def test_redirect_block_and_limit(self):
        for location in ("https://evil.example/?secret=x", "https://login.1688.com/", "/_____tmd_____/punish"):
            response = FakeResponse(code=302, headers={"Location": location})
            result, session = self.fetch([response])
            self.assertFalse(result.ok)
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(response.closed)
            self.assertNotIn("secret", json.dumps(result.to_dict()))
        responses = [FakeResponse(code=302, headers={"Location": URL}) for _ in range(3)]
        result, session = self.fetch(responses)
        self.assertEqual(len(session.calls), 3)
        self.assertTrue(all(r.closed for r in responses))
        self.assertEqual(result.reason, "redirect_limit_or_missing_location")

    def test_allowed_redirect(self):
        responses = [FakeResponse(code=302, headers={"Location": URL + "?trace=x"}), FakeResponse()]
        result, session = self.fetch(responses)
        self.assertTrue(result.ok)
        self.assertEqual(session.calls[1][0], URL)
        self.assertTrue(all(r.closed for r in responses))

    def test_retries_and_stream_error_cleanup(self):
        for first in (FakeResponse(code=503), FakeResponse(error=True), requests.RequestsError("SECRET")):
            with patch("product_sku.client.time.sleep"):
                result, session = self.fetch([first, FakeResponse()])
            self.assertTrue(result.ok)
            self.assertEqual(len(session.calls), 2)
            if isinstance(first, FakeResponse):
                self.assertTrue(first.closed)
        with patch("product_sku.client.time.sleep"):
            result, _ = self.fetch([requests.RequestsError("SECRET")] * 2)
        self.assertEqual(result.status, "network_failed")
        self.assertNotIn("SECRET", json.dumps(result.to_dict()))

    def test_size_encoding_and_http_cleanup(self):
        for response, reason in ((FakeResponse(headers={"Content-Length": "9999999"}), "response_too_large"),
                                 (FakeResponse(body=b"x" * 50), "response_too_large"),
                                 (FakeResponse(body=b"\xff"), "unsupported_encoding"),
                                 (FakeResponse(code=404), "unexpected_http_status")):
            result, _ = self.fetch([response], max_bytes=20)
            self.assertEqual(result.reason, reason)
            self.assertTrue(response.closed)

    def test_cli_success_failure_and_secret_safety(self):
        for result, exit_code in ((parse_detail(page(business()), URL), 0), (parse_detail("", URL), 1)):
            with patch("product_sku.client.ProductSkuClient") as factory:
                factory.return_value.__enter__.return_value.fetch.return_value = result
                with redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(main(["--url", URL, "--cookie-file", "not-read.json"]), exit_code)
                self.assertEqual(json.loads(stdout.getvalue())["product_id"], PID)
        with redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(main(["--url", "https://secret@evil/", "--cookie-file", "not-read.json"]), 2)
        self.assertNotIn("secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
