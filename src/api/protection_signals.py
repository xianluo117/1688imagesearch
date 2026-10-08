"""Classify upstream protection signals without consuming streamed response bodies."""
from __future__ import annotations

import math
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from image_search.errors import AuthenticationError, RateLimitError, RiskControlError


PAUSE_MARKERS = (
    "SESSION_EXPIRED", "NEED_LOGIN", "LOGIN_REQUIRED", "RGV587_ERROR",
    "FAIL_SYS_USER_VALIDATE", "FAIL_SYS_TRAFFIC_LIMIT", "CAPTCHA", "VALIDATE", "X5SEC",
    "RISK_CONTROL", "人机验证", "登录失效",
)
RATE_MARKERS = ("TOO_MANY_REQUEST", "RATE_LIMIT", "HTTP_429", "限流", "频繁")


def retry_after_seconds(value: object, now: float) -> float | None:
    """Accept RFC delay-seconds or a timezone-qualified HTTP date; never NaN/inf."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if re.fullmatch(r"[0-9]+", value):
            seconds = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0.0, date.astimezone(timezone.utc).timestamp() - now)
        return seconds if math.isfinite(seconds) and math.isfinite(now + seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _address_signal(value: str) -> str | None:
    try:
        parts = urlsplit(value)
        host, path = (parts.hostname or "").lower(), parts.path.lower()
    except ValueError:
        return None
    if host in {"login.1688.com", "login.taobao.com", "passport.1688.com"} or path.startswith(("/login", "/member/signin")):
        return "LOGIN_REQUIRED"
    if any(marker in path for marker in ("/_____tmd_____/", "/punish", "/challenge", "/captcha", "/validate")):
        return "RISK_CONTROL"
    return None


def response_signal(response) -> tuple[str, str] | None:
    headers = getattr(response, "headers", {}) or {}
    url = str(getattr(response, "url", "") or "")
    location = headers.get("Location", headers.get("location", ""))
    for address in (url, urljoin(url, str(location)) if location else ""):
        reason = _address_signal(address)
        if reason:
            return "paused", reason
    status = getattr(response, "status_code", None)
    if status == 401:
        return "paused", "LOGIN_REQUIRED"
    if status == 403:
        return "paused", "RISK_CONTROL"
    if status == 429:
        return "cooldown", "RATE_LIMITED"
    return None


def result_signal(value) -> tuple[str, str] | None:
    # Inspect protocol status fields only, not product titles or arbitrary HTML.
    if isinstance(value, dict):
        status, reason, ret = value.get("status", ""), value.get("reason", ""), value.get("ret", "")
    else:
        status, reason, ret = getattr(value, "status", ""), getattr(value, "reason", ""), getattr(value, "ret", "")
    text = f"{status} {reason} {ret}".upper()
    if any(marker in text for marker in PAUSE_MARKERS):
        return "paused", "LOGIN_REQUIRED" if "LOGIN" in text or "SESSION_EXPIRED" in text else "RISK_CONTROL"
    if any(marker in text for marker in RATE_MARKERS):
        return "cooldown", "RATE_LIMITED"
    if str(status).lower() == "access_restricted":
        return "paused", "RISK_CONTROL"
    return None


def error_signal(exc: Exception) -> tuple[str, str] | None:
    # Pause always wins even if an upstream error includes both kinds of marker.
    signal = result_signal({"reason": str(exc), "ret": getattr(exc, "ret", "")})
    if isinstance(exc, (AuthenticationError, RiskControlError)) or (signal and signal[0] == "paused"):
        return "paused", "LOGIN_REQUIRED" if isinstance(exc, AuthenticationError) else (signal[1] if signal else "RISK_CONTROL")
    response = getattr(exc, "response", None)
    response_match = response_signal(response) if response is not None else None
    if response_match and response_match[0] == "paused":
        return response_match
    if isinstance(exc, RateLimitError):
        return "cooldown", "RATE_LIMITED"
    return response_match or signal
