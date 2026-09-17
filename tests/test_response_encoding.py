"""Focused response charset regression tests; no network or database required."""

import unittest

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from src.api.response_encoding import JsonCharsetMiddleware


class JsonCharsetTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.add_middleware(JsonCharsetMiddleware)

        @app.get("/json")
        def json_result():
            return {"颜色": "卡其色", "spec_id": "01d9d07aae887f1125c37dc508490153"}

        @app.get("/error")
        def error_result():
            raise HTTPException(status_code=503, detail="服务器没有可用的会话")

        @app.get("/text")
        def text_result():
            return Response("中文", media_type="text/plain")

        @app.get("/explicit")
        def explicit_result():
            return Response(b"{}", headers={"content-type": "application/json; charset=utf-8"})

        @app.get("/problem")
        def problem_result():
            return Response(b"{}", media_type="application/problem+json")

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_json_is_utf8_and_preserves_values(self) -> None:
        response = self.client.get("/json")
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertIn("卡其色", response.content.decode("utf-8"))
        self.assertEqual(response.json()["颜色"], "卡其色")
        self.assertEqual(response.json()["spec_id"], "01d9d07aae887f1125c37dc508490153")
        self.assertEqual(int(response.headers["content-length"]), len(response.content))

    def test_error_response_is_utf8(self) -> None:
        response = self.client.get("/error")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.json()["detail"], "服务器没有可用的会话")

    def test_non_json_is_unchanged(self) -> None:
        response = self.client.get("/text")
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(response.text, "中文")

    def test_existing_charset_is_not_duplicated(self) -> None:
        response = self.client.get("/explicit")
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")

    def test_json_suffix_gets_charset(self) -> None:
        response = self.client.get("/problem")
        self.assertEqual(response.headers["content-type"], "application/problem+json; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
