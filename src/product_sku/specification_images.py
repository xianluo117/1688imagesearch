"""Option-scoped image metadata only; never fetch or guess image addresses."""
import ipaddress
import re
from urllib.parse import urlsplit

from .models import Sku, SpecificationImage


def safe_image_url(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        return None
    if re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.fragment or parts.port not in {None, 80, 443}):
            return None
        host = parts.hostname
        if host == "localhost" or "." not in host or host.endswith((".localhost", ".local")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
                return None
        else:
            if not address.is_global:
                return None
    except ValueError:
        return None
    return value


class OptionImages:
    """Collect only from models whose trade rows passed specification validation."""
    def __init__(self):
        self.candidates: dict[tuple, set[str | None]] = {}

    def add(self, sku: Sku, props: list[dict]) -> None:
        for spec, prop in zip(sku.specifications, props):
            key = (spec.position, spec.field, spec.name, spec.value)
            matches = [v for v in prop["value"] if v["name"] == spec.value]
            image = safe_image_url(matches[0].get("imageUrl")) if len(matches) == 1 else None
            self.candidates.setdefault(key, set()).add(image)

    def finish(self, skus: list[Sku]) -> list[SpecificationImage]:
        result = []
        seen = set()
        for sku in skus:
            for spec in sku.specifications:
                key = (spec.position, spec.field, spec.name, spec.value)
                if key in seen:
                    continue
                seen.add(key)
                urls = self.candidates.get(key, {None})
                image = next(iter(urls)) if len(urls) == 1 else None
                result.append(SpecificationImage(*key, image))
        return result
