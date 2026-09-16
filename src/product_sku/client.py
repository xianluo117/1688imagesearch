"""Bounded, isolated detail-page HTTP client (no MTOP/browser fallback)."""
import math
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests
from image_search.cookies import ImportedCookie, load_cookie_records

from .extraction import MAX_BYTES
from .models import SkuResult
from .parser import parse_detail
from .session import detail_cookie_jar
from .urls import address_status, normalize_url, redirect_target


class ProductSkuClient:
    def __init__(self, cookie_file: str | Path | None = None, *,
                 cookie_records: list[ImportedCookie] | None = None,
                 session: Any | None = None, timeout: float = 20,
                 network_retries: int = 1, max_bytes: int = MAX_BYTES):
        if (cookie_file is None) == (cookie_records is None):
            raise ValueError("provide_exactly_one_cookie_source")
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("invalid_timeout")
        if type(network_retries) is not int or not 0 <= network_retries <= 2:
            raise ValueError("invalid_retry_limit")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
            raise ValueError("invalid_size_limit")
        try:
            records = load_cookie_records(cookie_file) if cookie_file is not None else cookie_records
            jar = detail_cookie_jar(records)
        except Exception:
            raise ValueError("cookie_load_failed") from None
        self._owned = session is None
        self.session = requests.Session(impersonate="chrome", trust_env=False) if self._owned else session
        try:
            # curl_cffi accepts a CookieJar; do not flatten it to name/value pairs.
            self.session.cookies = requests.Cookies(jar)
        except Exception:
            if self._owned:
                self.session.close()
            raise ValueError("cookie_session_setup_failed") from None
        self.timeout = timeout
        self.network_retries = network_retries
        self.max_bytes = max_bytes
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if not self._closed and self._owned:
            self.session.close()
        self._closed = True

    def fetch(self, url: str) -> SkuResult:
        product_id, canonical = normalize_url(url)
        if self._closed:
            raise ValueError("client_closed")

        def failure(status: str, reason: str) -> SkuResult:
            return SkuResult(product_id, canonical, status, reason)

        current = canonical
        redirects = 0
        retries = 0
        while True:
            response = None
            retry = False
            try:
                response = self.session.get(
                    current, allow_redirects=False, stream=True, timeout=self.timeout,
                    headers={"Accept": "text/html,application/xhtml+xml",
                             "Accept-Language": "zh-CN,zh;q=0.9"},
                )
                code = response.status_code
                final_address = str(getattr(response, "url", "") or current)
                status = address_status(final_address)
                if status:
                    return failure(status, "explicit_access_address")
                try:
                    if normalize_url(final_address)[0] != product_id:
                        raise ValueError
                except ValueError:
                    return failure("access_restricted", "unexpected_response_address")
                if code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    if not location or redirects >= 2:
                        return failure("access_restricted", "redirect_limit_or_missing_location")
                    target, status = redirect_target(current, location, product_id)
                    if status:
                        return failure(status, "blocked_redirect")
                    current = target
                    redirects += 1
                    continue
                if code in {403, 429}:
                    return failure("access_restricted", f"http_{code}")
                if code == 401:
                    return failure("login_required", "http_401")
                if code in {500, 502, 503, 504}:
                    retry = True
                elif code != 200:
                    return failure("network_failed", "unexpected_http_status")
                else:
                    length = response.headers.get("Content-Length", "")
                    if length.isdigit() and int(length) > self.max_bytes:
                        return failure("parse_failed", "response_too_large")
                    chunks = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=16384):
                        size += len(chunk)
                        if size > self.max_bytes:
                            return failure("parse_failed", "response_too_large")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    # Decode losslessly or fail rather than corrupt specification values.
                    try:
                        text = body.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        content_type = response.headers.get("Content-Type", "").lower()
                        if "gbk" not in content_type and "gb2312" not in content_type:
                            return failure("parse_failed", "unsupported_encoding")
                        try:
                            text = body.decode("gb18030")
                        except UnicodeDecodeError:
                            return failure("parse_failed", "invalid_encoding")
                    return parse_detail(text, canonical)
            except requests.RequestsError:
                retry = True
            finally:
                if response is not None:
                    response.close()
            if retry:
                if retries >= self.network_retries:
                    return failure("network_failed", "network_retries_exhausted")
                retries += 1
                time.sleep(0.25 * retries)
