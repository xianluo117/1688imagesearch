from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from request_control import RequestControl, control_scope, disable_transport_retries, notify

import requests
from curl_cffi import requests as curl_requests

from .errors import (
    AuthenticationError,
    MtopError,
    MtopNetworkError,
    ProtocolError,
    RateLimitError,
    RiskControlError,
    TokenError,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_APP_KEY = "12574478"
DEFAULT_ENDPOINT = "https://h5api.m.1688.com/h5"
DEFAULT_USER_AGENT = None
TOKEN_ERROR_MARKERS = (
    "FAIL_SYS_TOKEN_EXOIRED",
    "FAIL_SYS_TOKEN_EXPIRED",
    "FAIL_SYS_ILLEGAL_ACCESS",
    "FAIL_SYS_INVALID_SIGNATURE",
    "TOKEN_EMPTY",
)
AUTH_ERROR_MARKERS = (
    "SESSION_EXPIRED",
    "FAIL_SYS_SESSION_EXPIRED",
    "NEED_LOGIN",
    "LOGIN_REQUIRED",
)
RISK_ERROR_MARKERS = (
    "RGV587_ERROR",
    "FAIL_SYS_USER_VALIDATE",
    "FAIL_SYS_TRAFFIC_LIMIT",
    "CAPTCHA",
    "VALIDATE",
    "X5SEC",
)
RATE_LIMIT_MARKERS = ("TOO_MANY_REQUEST", "RATE_LIMIT", "限流", "频繁")


@dataclass(frozen=True)
class MtopRequest:
    api: str
    version: str
    data: dict[str, Any]
    method: str = "POST"
    request_type: str = "originaljson"
    data_type: str = "jsonp"
    timeout_ms: int = 20_000
    extra_query: dict[str, str] | None = None


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def calculate_sign(token: str, timestamp_ms: str, app_key: str, data: str) -> str:
    source = f"{token}&{timestamp_ms}&{app_key}&{data}"
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def extract_token(cookie_value: str | None) -> str:
    if not cookie_value:
        raise TokenError("Cookie 中缺少 _m_h5_tk", ret=["TOKEN_EMPTY"])
    token = cookie_value.split("_", 1)[0]
    if not token:
        raise TokenError("_m_h5_tk 格式无效", ret=["TOKEN_EMPTY"])
    return token


def _ret_text(payload: dict[str, Any]) -> tuple[list[str], str]:
    ret = payload.get("ret")
    if isinstance(ret, str):
        ret_list = [ret]
    elif isinstance(ret, list):
        ret_list = [str(item) for item in ret]
    else:
        ret_list = []
    return ret_list, " | ".join(ret_list)


def ensure_mtop_success(payload: dict[str, Any]) -> None:
    ret, text = _ret_text(payload)
    upper = text.upper()
    if not ret or any(item.upper().startswith("SUCCESS") for item in ret):
        return

    kwargs = {"ret": ret, "payload": payload}
    if any(marker in upper for marker in TOKEN_ERROR_MARKERS):
        raise TokenError(text, **kwargs)
    if any(marker in upper for marker in AUTH_ERROR_MARKERS):
        raise AuthenticationError(text, **kwargs)
    if any(marker in upper for marker in RATE_LIMIT_MARKERS):
        raise RateLimitError(text, **kwargs)
    if any(marker in upper for marker in RISK_ERROR_MARKERS):
        raise RiskControlError(text, **kwargs)
    raise MtopError(text or "MTOP 返回未知错误", **kwargs)


def parse_json_or_jsonp(response: Any) -> dict[str, Any]:
    text = response.text.lstrip("\ufeff \t\r\n")
    try:
        payload = response.json()
    except (requests.JSONDecodeError, json.JSONDecodeError, ValueError):
        start = text.find("(")
        end = text.rfind(")")
        if start < 0 or end <= start:
            preview = text[:300].replace("\n", " ")
            raise ProtocolError(f"MTOP 响应不是 JSON/JSONP: {preview}")
        try:
            payload = json.loads(text[start + 1 : end])
        except json.JSONDecodeError as exc:
            preview = text[:300].replace("\n", " ")
            raise ProtocolError(f"MTOP JSONP 解析失败: {preview}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("MTOP 响应根节点不是对象")
    return payload


class MtopClient:
    def __init__(
        self,
        session: Any,
        *,
        app_key: str = DEFAULT_APP_KEY,
        endpoint: str = DEFAULT_ENDPOINT,
        user_agent: str | None = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
        network_retries: int = 2,
        response_hook: Callable[[MtopRequest, dict[str, Any]], None] | None = None,
        request_control: RequestControl | None = None,
    ) -> None:
        self.session = session
        self.request_control = request_control
        disable_transport_retries(session)
        self.app_key = app_key
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.network_retries = max(0, network_retries)
        self.response_hook = response_hook
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://air.1688.com",
            "Referer": "https://air.1688.com/",
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        self.session.headers.update(headers)

    def _token_cookie(self) -> str | None:
        jar = getattr(self.session.cookies, "jar", self.session.cookies)
        values = [cookie.value for cookie in jar if cookie.name == "_m_h5_tk"]
        return values[-1] if values else None

    def _signed_query(self, request: MtopRequest, data_text: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        token = extract_token(self._token_cookie())
        sign = calculate_sign(token, timestamp_ms, self.app_key, data_text)
        query: dict[str, str] = {
            "jsv": "2.7.2",
            "appKey": self.app_key,
            "t": timestamp_ms,
            "sign": sign,
            "api": request.api,
            "v": request.version,
            "type": request.request_type,
            "dataType": request.data_type,
            "timeout": str(request.timeout_ms),
        }
        if request.extra_query:
            query.update(request.extra_query)
        # These fields must never reuse an override or a redirect's stale signature.
        query.update(t=timestamp_ms, sign=sign, appKey=self.app_key)
        return query

    @staticmethod
    def _check_access_address(url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
        if host in {"login.1688.com", "login.taobao.com", "passport.1688.com"} or path.startswith(
            ("/login", "/member/signin")
        ):
            raise AuthenticationError("MTOP 重定向至登录页面", ret=["LOGIN_REQUIRED"])
        if any(marker in path for marker in ("/_____tmd_____/", "/punish", "/challenge", "/captcha", "/validate")):
            raise RiskControlError("MTOP 重定向至验证页面", ret=["CAPTCHA"])

    def _request_once(self, request: MtopRequest, data_text: str) -> dict[str, Any]:
        url = f"{self.endpoint}/{request.api}/{request.version}/"
        origin = urlsplit(url)
        method = request.method.upper()
        if method not in {"GET", "POST"}:
            raise ProtocolError(f"不支持的 HTTP 方法: {method}")
        redirect_query: dict[str, str] = {}
        for redirects in range(3):
            notify(self.request_control, "before_request")
            query = {**redirect_query, **self._signed_query(request, data_text)}
            if method == "GET":
                response = self.session.get(
                    url, params={**query, "data": data_text},
                    timeout=self.timeout, allow_redirects=False,
                )
            else:
                response = self.session.post(
                    url, params=query, data={"data": data_text},
                    timeout=self.timeout, allow_redirects=False,
                )
            try:
                notify(self.request_control, "observe_response", response)
                self._check_access_address(str(getattr(response, "url", "") or url))
                if response.status_code == 429:
                    raise RateLimitError("MTOP HTTP 429", ret=["HTTP_429"])
                if response.status_code == 401:
                    raise AuthenticationError("MTOP HTTP 401", ret=["LOGIN_REQUIRED"])
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    target = urljoin(url, location)
                    self._check_access_address(target)
                    if not location or redirects >= 2:
                        raise ProtocolError("MTOP 重定向超过上限或缺少 Location")
                    parts = urlsplit(target)
                    # Only replay signed API requests within the original MTOP origin.
                    if (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc) or not parts.path.startswith(origin.path.rsplit("/", 3)[0] + "/"):
                        raise ProtocolError("MTOP 重定向目标不是原站 API")
                    redirect_query = dict(parse_qsl(parts.query, keep_blank_values=True))
                    for key in ("t", "sign", "appKey", "data"):
                        redirect_query.pop(key, None)
                    url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                    if response.status_code == 303 or (response.status_code in {301, 302} and method == "POST"):
                        method = "GET"
                    continue
                response.raise_for_status()
                return parse_json_or_jsonp(response)
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
        raise ProtocolError("MTOP 重定向超过上限")

    def request(self, request: MtopRequest) -> dict[str, Any]:
        with control_scope():
            return self._request(request)

    def _request(self, request: MtopRequest) -> dict[str, Any]:
        data_text = compact_json(request.data)
        token_retry_used = False
        network_attempt = 0

        while True:
            token_before = self._token_cookie()
            attempt_number = network_attempt + 1
            started_at = time.monotonic()
            LOGGER.info(
                "MTOP 请求开始: api=%s method=%s attempt=%d/%d payload_bytes=%d",
                request.api,
                request.method.upper(),
                attempt_number,
                self.network_retries + 1,
                len(data_text.encode("utf-8")),
            )
            try:
                payload = self._request_once(request, data_text)
                LOGGER.info(
                    "MTOP 请求成功: api=%s method=%s attempt=%d elapsed=%.2fs",
                    request.api,
                    request.method.upper(),
                    attempt_number,
                    time.monotonic() - started_at,
                )
                ensure_mtop_success(payload)
                if self.response_hook:
                    self.response_hook(request, payload)
                return payload
            except TokenError as exc:
                notify(self.request_control, "observe_error", exc)
                token_after = self._token_cookie()
                if not token_retry_used and token_after and token_after != token_before:
                    token_retry_used = True
                    LOGGER.info("MTOP Token 已刷新，正在重新签名重试")
                    continue
                raise
            except ProtocolError as exc:
                notify(self.request_control, "observe_error", exc)
                raise
            except (requests.RequestException, curl_requests.RequestsError) as exc:
                notify(self.request_control, "observe_error", exc)
                elapsed = time.monotonic() - started_at
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                status = status if isinstance(status, int) and status > 0 else None
                retryable = status is None or status >= 500
                LOGGER.warning(
                    "MTOP 请求异常: api=%s method=%s attempt=%d/%d elapsed=%.2fs status=%s error_type=%s error=%s",
                    request.api,
                    request.method.upper(),
                    attempt_number,
                    self.network_retries + 1,
                    elapsed,
                    status if status is not None else "network",
                    type(exc).__name__,
                    str(exc),
                )
                if not retryable or network_attempt >= self.network_retries:
                    kind = f"HTTP {status}" if status is not None else "网络"
                    raise MtopNetworkError(
                        f"MTOP {kind}请求失败: api={request.api}, method={request.method.upper()}, "
                        f"attempts={attempt_number}, elapsed={elapsed:.2f}s, error={exc}"
                    ) from exc
                delay = min(8.0, 0.8 * (2**network_attempt))
                network_attempt += 1
                LOGGER.warning(
                    "MTOP 将重试: api=%s delay=%.1fs next_attempt=%d/%d",
                    request.api,
                    delay,
                    network_attempt + 1,
                    self.network_retries + 1,
                )
                time.sleep(delay)
