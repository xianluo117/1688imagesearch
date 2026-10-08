"""Independent synthetic request-control tests; never access the network."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests
from curl_cffi import requests as curl_requests
from image_search.cookies import ImportedCookie
from image_search.errors import AuthenticationError, ProtocolError, RateLimitError, RiskControlError, TokenError
from image_search.image_search import ImageSearchClient, SearchOptions
from image_search.mtop import MtopClient, MtopRequest, calculate_sign, compact_json
from product_sku.client import ProductSkuClient
from request_control import disable_transport_retries

URL = "https://detail.1688.com/offer/123.html"
API_URL = "https://h5api.m.1688.com/h5/example.api/1.0/"
COOKIE = ImportedCookie("cookie2", "synthetic", expires=4102444800)
TOKEN = ImportedCookie("_m_h5_tk", "old_4102444800", expires=4102444800)
SUCCESS = {"ret": ["SUCCESS::ok"], "data": {}}
REQUEST = MtopRequest("example.api", "1.0", {"value": 1})
BODY = '<script type="application/json">' + json.dumps({
    "offerId": "123", "pieceWeightScaleInfo": [{"skuId": "1", "sku1": "蓝色"}]
}) + '</script>'


class Response:
    def __init__(self, code=200, payload=None, headers=None, body=BODY, url="", stream_error=None):
        self.status_code = code
        self.payload = SUCCESS if payload is None else payload
        self.text = json.dumps(self.payload)
        self.headers = headers or {}
        self.body = body.encode()
        self.url = url
        self.closed = False
        self.stream_error = stream_error

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("synthetic", response=self)

    def iter_content(self, chunk_size):
        yield self.body[:10]
        if self.stream_error:
            raise self.stream_error
        yield self.body[10:]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.cookies.set("_m_h5_tk", "old_4102444800", domain=".1688.com")
        self.calls = []

    def get(self, url, **kwargs):
        return self.send("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self.send("POST", url, kwargs)

    def send(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if callable(response):
            response = response(self)
        if isinstance(response, Exception):
            raise response
        return response


class Control:
    def __init__(self):
        self.permits = 0
        self.responses = []
        self.errors = []
        self.results = []

    def before_request(self):
        self.permits += 1

    def observe_response(self, response):
        self.responses.append(response)

    def observe_error(self, exc):
        self.errors.append(exc)

    def observe_result(self, result):
        self.results.append(result)


class RequestControlTests(unittest.TestCase):
    def mtop(self, responses, control=None, **kwargs):
        session = Session(responses)
        return MtopClient(session, request_control=control, **kwargs), session

    def sku(self, responses, control=None):
        session = Session(responses)
        return ProductSkuClient(cookie_records=[COOKIE], session=session, request_control=control), session

    def test_signature_after_each_permission_and_redirect(self):
        control = Control()
        clock = [1]
        first = Response(302, headers={"Location": API_URL + "?t=1&sign=stale&x=2"})
        client, session = self.mtop([first, Response()], control)
        def permit():
            control.permits += 1
            clock[0] += 10
            session.cookies.set("_m_h5_tk", f"token{control.permits}_99", domain=".1688.com")
        control.before_request = permit
        with patch("image_search.mtop.time.time", side_effect=lambda: clock[0]):
            client.request(REQUEST)
        self.assertEqual(control.permits, 2)
        self.assertEqual([c[0] for c in session.calls], ["POST", "GET"])
        for number, (_, url, kwargs) in enumerate(session.calls, 1):
            query = kwargs["params"]
            self.assertEqual(query["t"], str((1 + 10 * number) * 1000))
            self.assertEqual(query["sign"], calculate_sign(f"token{number}", query["t"], client.app_key, compact_json(REQUEST.data)))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("?", url)
        self.assertTrue(first.closed)

    def test_redirect_method_preservation_and_limit(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                control = Control()
                responses = [Response(code, headers={"Location": API_URL}) for _ in range(3)]
                client, session = self.mtop(responses, control)
                with self.assertRaises(ProtocolError):
                    client.request(REQUEST)
                self.assertEqual(control.permits, 3)
                self.assertEqual(session.calls[1][0], "POST" if code in (307, 308) else "GET")
                self.assertTrue(all(r.closed for r in responses))

    def test_login_challenge_and_foreign_redirect_not_followed(self):
        for target, error in (("https://login.1688.com/", AuthenticationError),
                              ("/_____tmd_____/punish", RiskControlError),
                              ("https://other.example/h5/", ProtocolError)):
            control = Control()
            client, session = self.mtop([Response(302, headers={"Location": target})], control)
            with self.assertRaises(error):
                client.request(REQUEST)
            self.assertEqual(len(session.calls), 1)
            self.assertIsInstance(control.errors[0], error)

    def test_network_retry_and_http_retry(self):
        for first in (requests.ConnectionError("offline"), Response(503)):
            control = Control()
            client, session = self.mtop([first, Response()], control)
            with patch("image_search.mtop.time.sleep"):
                client.request(REQUEST)
            self.assertEqual(control.permits, 2)
            self.assertEqual(len(control.errors), 1)
            self.assertEqual(len(session.calls), 2)

    def test_token_refresh_retry(self):
        control = Control()
        def refresh(session):
            session.cookies.set("_m_h5_tk", "new_99", domain=".1688.com")
            return Response(payload={"ret": ["FAIL_SYS_TOKEN_EXPIRED"]})
        client, session = self.mtop([refresh, Response()], control)
        client.request(REQUEST)
        self.assertEqual(control.permits, 2)
        self.assertIsInstance(control.errors[0], TokenError)
        self.assertNotEqual(session.calls[0][2]["params"]["sign"], session.calls[1][2]["params"]["sign"])

    def test_mtop_rate_limit_reports_response_and_error_without_retry(self):
        control = Control()
        response = Response(429, headers={"Retry-After": "120"})
        client, session = self.mtop([response], control)
        with self.assertRaises(RateLimitError):
            client.request(REQUEST)
        self.assertEqual(control.responses, [response])
        self.assertEqual(control.responses[0].headers["Retry-After"], "120")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(response.closed)

    def test_mtop_business_errors_observed(self):
        for marker, error in (("NEED_LOGIN", AuthenticationError), ("CAPTCHA", RiskControlError), ("RATE_LIMIT", RateLimitError)):
            control = Control()
            client, _ = self.mtop([Response(payload={"ret": [marker]})], control)
            with self.assertRaises(error):
                client.request(REQUEST)
            self.assertIsInstance(control.errors[0], error)

    def test_image_polling_uses_same_control(self):
        control = Control()
        ready = {"ret": ["SUCCESS"], "data": {"OFFER": {"items": [{"offerId": "123", "title": "test"}]}}}
        pending = {"ret": ["SUCCESS"], "data": {"OFFER": {"items": []}}}
        session = Session([Response(payload=pending), Response(payload=ready)])
        cookies = [COOKIE, TOKEN, ImportedCookie("_m_h5_tk_enc", "synthetic", expires=4102444800)]
        client = ImageSearchClient(cookie_records=cookies, session=session,
                                   request_control=control, options=SearchOptions(request_interval=0))
        result = client.search_image_id("image")
        self.assertEqual(result.pages_requested, 2)
        self.assertEqual(control.permits, 2)
        self.assertEqual(len(session.calls), 2)

    def test_sku_redirect_network_and_stream_retries(self):
        for first in (Response(302, headers={"Location": URL}), Response(503),
                      curl_requests.RequestsError("offline"),
                      Response(stream_error=curl_requests.RequestsError("stream"))):
            control = Control()
            client, session = self.sku([first, Response()], control)
            with patch("product_sku.client.time.sleep"):
                result = client.fetch(URL)
            self.assertTrue(result.ok)
            self.assertEqual(control.permits, 2)
            self.assertEqual(control.results, [result])
            self.assertTrue(all(not call[2]["allow_redirects"] for call in session.calls))
            if isinstance(first, Response):
                self.assertTrue(first.closed)

    def test_sku_rate_limit_login_and_captcha_results(self):
        for response, status in ((Response(429, headers={"Retry-After": "90"}), "access_restricted"),
                                 (Response(302, headers={"Location": "https://login.1688.com/"}), "login_required"),
                                 (Response(body='<script src="/_____tmd_____/punish"></script>'), "access_restricted")):
            control = Control()
            client, session = self.sku([response], control)
            result = client.fetch(URL)
            self.assertEqual(result.status, status)
            self.assertEqual(control.responses, [response])
            self.assertEqual(control.results, [result])
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(response.closed)

    def test_callback_exceptions_preserve_identity_and_never_retry(self):
        for kind in ("mtop", "sku"):
            hooks = ["before_request", "observe_response", "observe_error"]
            if kind == "sku":
                hooks.append("observe_result")
            for hook in hooks:
                for error in (TimeoutError("cancel/deadline"), curl_requests.RequestsError("callback"), TokenError("callback")):
                    with self.subTest(kind=kind, hook=hook, error=type(error).__name__):
                        control = Control()
                        def abort(*args):
                            raise error
                        setattr(control, hook, abort)
                        response = Response()
                        upstream = curl_requests.RequestsError("upstream") if hook == "observe_error" else response
                        client, session = getattr(self, kind)([upstream], control)
                        with self.assertRaises(type(error)) as caught:
                            client.request(REQUEST) if kind == "mtop" else client.fetch(URL)
                        self.assertIs(caught.exception, error)
                        self.assertEqual(len(session.calls), 0 if hook == "before_request" else 1)
                        if hook in {"observe_response", "observe_result"}:
                            self.assertTrue(response.closed)

    def test_cancellation_between_requests_stops_next_network_call(self):
        for kind in ("mtop", "sku"):
            control = Control()
            def permit():
                control.permits += 1
                if control.permits == 2:
                    raise TimeoutError("deadline")
            control.before_request = permit
            client, session = getattr(self, kind)([Response(503)], control)
            with patch("time.sleep"), self.assertRaises(TimeoutError):
                client.request(REQUEST) if kind == "mtop" else client.fetch(URL)
            self.assertEqual(len(session.calls), 1)

    def test_no_control_and_before_only_control(self):
        class Minimal:
            def before_request(self):
                pass
        for control in (None, Minimal()):
            client, _ = self.mtop([Response()], control)
            self.assertEqual(client.request(REQUEST), SUCCESS)
            client, _ = self.sku([Response()], control)
            self.assertTrue(client.fetch(URL).ok)

    def test_standard_requests_implicit_retries_disabled(self):
        with requests.Session() as session:
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=3))
            disable_transport_retries(session)
            self.assertTrue(all(adapter.max_retries.total == 0 for adapter in session.adapters.values()))


if __name__ == "__main__":
    unittest.main()
