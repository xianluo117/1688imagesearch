"""Reuse imported cookie records without MTOP requirements."""
import re
import time
from collections.abc import Iterable

from image_search.cookies import ImportedCookie, build_cookie_jar


def detail_cookie_jar(records: Iterable[ImportedCookie], now: float | None = None):
    now = time.time() if now is None else now
    accepted = []
    for record in records:
        domain = record.domain.lower()
        if domain not in {"1688.com", ".1688.com", "detail.1688.com", ".detail.1688.com"}:
            continue
        if record.expires is not None and record.expires <= now:
            continue
        if not record.path.startswith("/") or re.search(r"[\x00-\x20\x7f]", record.path):
            continue
        if not re.fullmatch(r"[^\s()<>@,;:\\\"/\[\]?={}\x00-\x1f\x7f]+", record.name):
            continue
        if re.search(r"[\x00-\x1f\x7f;]", record.value):
            continue
        accepted.append(record)
    if not accepted:
        raise ValueError("no_usable_cookies")
    return build_cookie_jar(accepted)
