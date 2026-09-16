"""Strict URL validation. Redirect targets are never fetched before validation."""
import re
from urllib.parse import urljoin, urlsplit

HOST = "detail.1688.com"


def normalize_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("invalid_product_url")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or parts.hostname != HOST:
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
        # Even explicit default ports are excluded from the accepted input grammar.
        if parts.netloc.lower() != HOST:
            raise ValueError
        match = re.fullmatch(r"/offer/([1-9][0-9]{0,29})\.html", parts.path)
        if not match:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("invalid_product_url") from None
    product_id = match.group(1)
    return product_id, f"https://{HOST}/offer/{product_id}.html"


def address_status(value: str) -> str | None:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        path = parts.path.lower()
    except ValueError:
        return "access_restricted"
    if "/_____tmd_____/" in path or path.startswith(("/punish", "/challenge")):
        return "access_restricted"
    if host in {"login.1688.com", "login.taobao.com", "passport.1688.com"}:
        return "login_required"
    if host == HOST and path.startswith(("/login", "/member/signin")):
        return "login_required"
    return None


def redirect_target(current: str, location: str, product_id: str) -> tuple[str | None, str | None]:
    target = urljoin(current, location)
    status = address_status(target)
    if status:
        return None, status
    try:
        found_id, canonical = normalize_url(target)
        if found_id != product_id or urlsplit(target).scheme != "https":
            raise ValueError
    except ValueError:
        return None, "access_restricted"
    return canonical, None
