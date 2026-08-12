from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from requests.cookies import RequestsCookieJar, create_cookie

from .errors import AuthenticationError, ProtocolError

DEFAULT_DOMAIN = ".1688.com"
REQUIRED_COOKIE_GROUPS = (
    ("cookie2", "cookie1"),
    ("_m_h5_tk",),
    ("_m_h5_tk_enc",),
)


@dataclass(frozen=True)
class ImportedCookie:
    name: str
    value: str
    domain: str = DEFAULT_DOMAIN
    path: str = "/"
    expires: int | None = None
    secure: bool = True
    rest: dict[str, Any] | None = None


def _parse_cookie_string(value: str) -> list[ImportedCookie]:
    cookies: list[ImportedCookie] = []
    for part in value.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, cookie_value = item.split("=", 1)
        name = name.strip()
        if name:
            cookies.append(ImportedCookie(name=name, value=cookie_value.strip()))
    return cookies


def _normalize_expiry(value: Any) -> int | None:
    if value in (None, "", -1, 0):
        return None
    try:
        expiry = float(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Cookie expires 字段无效: {value!r}") from exc
    if expiry > 10_000_000_000:
        expiry /= 1000
    return int(expiry)


def _normalize_cookie(item: dict[str, Any]) -> ImportedCookie:
    name = str(item.get("name", "")).strip()
    if not name:
        raise ProtocolError("Cookie 记录缺少 name")
    if "value" not in item:
        raise ProtocolError(f"Cookie {name!r} 缺少 value")

    same_site = item.get("sameSite") or item.get("same_site")
    rest: dict[str, Any] = {}
    if same_site:
        rest["SameSite"] = str(same_site)
    if item.get("httpOnly") or item.get("http_only"):
        rest["HttpOnly"] = True

    return ImportedCookie(
        name=name,
        value=str(item["value"]),
        domain=str(item.get("domain") or DEFAULT_DOMAIN),
        path=str(item.get("path") or "/"),
        expires=_normalize_expiry(item.get("expires", item.get("expirationDate"))),
        secure=bool(item.get("secure", True)),
        rest=rest,
    )


def parse_cookie_payload(payload: Any) -> list[ImportedCookie]:
    if isinstance(payload, str):
        return _parse_cookie_string(payload)
    if isinstance(payload, dict):
        if isinstance(payload.get("cookies"), list):
            payload = payload["cookies"]
        elif all(isinstance(key, str) for key in payload):
            return [ImportedCookie(name=key, value=str(value)) for key, value in payload.items()]
    if not isinstance(payload, list):
        raise ProtocolError("Cookie JSON 必须是数组、cookies 数组包装对象或名称到值的对象")

    records = []
    for item in payload:
        if not isinstance(item, dict):
            raise ProtocolError("Cookie 数组中的每一项必须是对象")
        records.append(_normalize_cookie(item))
    if not records:
        raise ProtocolError("没有解析到任何 Cookie")
    return records


def load_cookie_records(path: str | Path) -> list[ImportedCookie]:
    cookie_path = Path(path)
    if not cookie_path.is_file():
        raise ProtocolError(f"Cookie 文件不存在: {cookie_path}")

    raw = cookie_path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ProtocolError("Cookie 文件为空")

    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    return parse_cookie_payload(payload)


def build_cookie_jar(records: Iterable[ImportedCookie]) -> RequestsCookieJar:
    jar = RequestsCookieJar()
    count = 0
    for record in records:
        jar.set_cookie(
            create_cookie(
                name=record.name,
                value=record.value,
                domain=record.domain,
                path=record.path,
                secure=record.secure,
                expires=record.expires,
                rest=record.rest or {},
            )
        )
        count += 1
    if not count:
        raise ProtocolError("没有解析到任何 Cookie")
    return jar


def load_cookie_records_into_session(
    session: Any,
    records: Iterable[ImportedCookie],
) -> set[str]:
    names: set[str] = set()
    for record in records:
        session.cookies.set(
            record.name,
            record.value,
            domain=record.domain,
            path=record.path,
            secure=record.secure,
        )
        names.add(record.name)
    if not names:
        raise ProtocolError("没有解析到任何 Cookie")
    validate_cookie_names(names)
    return names


def load_cookies(session: Any, path: str | Path) -> set[str]:
    return load_cookie_records_into_session(session, load_cookie_records(path))


def validate_cookie_names(names: set[str]) -> None:
    missing = ["/".join(group) for group in REQUIRED_COOKIE_GROUPS if not any(name in names for name in group)]
    if missing:
        raise AuthenticationError(
            "Cookie 文件缺少必要登录或 MTOP 字段: " + ", ".join(missing),
            ret=["COOKIE_FIELDS_MISSING"],
        )


def cookie_names(session: Any) -> set[str]:
    jar = getattr(session.cookies, "jar", session.cookies)
    return {cookie.name for cookie in jar}
