"""Bounded, isolated detail-page HTTP client (no MTOP/browser fallback)."""
import math
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests
from requests import RequestException
from request_control import RequestControl, control_scope, disable_transport_retries, notify
from image_search.cookies import ImportedCookie, load_cookie_records

from error_details import response_details
from .extraction import MAX_BYTES
from .detail_images import enrich_detail_images
from .html_encoding import decode_html
from .models import SkuResult
from .parser import parse_detail
from .session import detail_cookie_jar
from .urls import address_status, normalize_url, redirect_target


class ProductSkuClient:
    def __init__(self, cookie_file: str | Path | None = None, *,
                 cookie_records: list[ImportedCookie] | None = None,
                 session: Any | None = None, timeout: float = 20,
                 network_retries: int = 1, max_bytes: int = MAX_BYTES,
                 request_control: RequestControl | None = None, proxy_url: str = ""):
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
        self.session = requests.Session(impersonate="chrome", trust_env=False, proxies={"all": proxy_url}) if self._owned else session
        try:
            # curl_cffi accepts a CookieJar; do not flatten it to name/value pairs.
            self.session.cookies = requests.Cookies(jar)
        except Exception:
            if self._owned:
                self.session.close()
            raise ValueError("cookie_session_setup_failed") from None
        self.request_control = request_control
        disable_transport_retries(self.session)
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
        with control_scope():
            result = self._fetch(url)
            notify(self.request_control, "observe_result", result)
            return result

    def _fetch(self, url: str) -> SkuResult:
        product_id, canonical = normalize_url(url)
        if self._closed:
            raise ValueError("client_closed")

        def capture(reason):
            notify(self.request_control, "capture_details", {
                "stage": "sku_detail", "request_url": current,
                "http": response_details(response, body=b''.join(chunks), complete=body_complete, reason=reason),
            })

        def failure(status: str, reason: str) -> SkuResult:
            nonlocal body_complete
            if response is not None and not chunks and reason != "response_too_large":
                size = 0
                try:
                    for chunk in response.iter_content(chunk_size=16384):
                        chunks.append(chunk[:max(0, self.max_bytes - size)])
                        size += len(chunk)
                        if size > self.max_bytes:
                            break
                    else:
                        body_complete = True
                except (requests.RequestsError, RequestException) as exc:
                    notify(self.request_control, "observe_error", exc)
            capture(reason)
            return SkuResult(product_id, canonical, status, reason)

        current = canonical
        redirects = 0
        retries = 0
        while True:
            response = None
            chunks = []
            body_complete = False
            retry = False
            notify(self.request_control, "before_request")
            try:
                response = self.session.get(
                    current, allow_redirects=False, stream=True, timeout=self.timeout,
                    accept_encoding="gzip, deflate, br",
                    headers={"Accept": "text/html,application/xhtml+xml",
                             "Accept-Language": "zh-CN,zh;q=0.9"},
                )
                notify(self.request_control, "observe_response", response)
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
                            chunks.append(chunk[:max(0, self.max_bytes - (size - len(chunk)))])
                            return failure("parse_failed", "response_too_large")
                        chunks.append(chunk)
                    body_complete = True
                    body = b"".join(chunks)
                    try:
                        text = decode_html(body, response.headers.get("Content-Type", ""))
                    except ValueError as exc:
                        return failure("parse_failed", str(exc))
                    result = parse_detail(text, canonical)
                    if result.status not in ("success", "partial_success") or not result.skus:
                        capture(result.reason)
                    response.close()
                    response = None
                    return enrich_detail_images(self, text, result)
            except (requests.RequestsError, RequestException) as exc:
                notify(self.request_control, "observe_error", exc)
                if retries >= self.network_retries:
                    return failure("network_failed", "network_retries_exhausted")
                retry = True
            finally:
                if response is not None:
                    response.close()
            if retry:
                if retries >= self.network_retries:
                    return failure("network_failed", "network_retries_exhausted")
                retries += 1
                time.sleep(0.25 * retries)
