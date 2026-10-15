"""Product-description image links and best-effort description retrieval."""
import json
import re
from html.parser import HTMLParser

from request_control import notify

from .extraction import Page, json_roots
from .specification_images import safe_image_url


def image_url(value):
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("//"):
            value = "https:" + value
    return safe_image_url(value)


class DescriptionImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            return
        attrs = dict(attrs)
        for key in ("data-src", "data-lazy-src", "data-original", "src"):
            url = image_url(attrs.get(key))
            if url:
                if url not in self.seen:
                    self.seen.add(url)
                    self.images.append(url)
                break


def parse_description(text: str) -> list[str]:
    """Read HTML, JSON or the observed offer_details assignment without JS execution."""
    text = text.lstrip("\ufeff \t\r\n")
    assignment = re.match(r"(?:var\s+)?offer_details\s*=\s*", text)
    if assignment or text.startswith("{"):
        payload, _ = json.JSONDecoder().raw_decode(text[assignment.end():] if assignment else text)
        text = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise ValueError("missing_description_content")
    parser = DescriptionImages()
    parser.feed(text)
    parser.close()
    return parser.images


def description_url(text: str, product_id: str) -> str | None:
    # Reuse the same product ownership checks as the SKU parser.
    from .parser import _arrays, _path

    roots, _ = json_roots(Page(text))
    verified = []
    _arrays(roots, product_id, verified_roots=verified)
    urls = []
    for root in verified:
        fields = _path(root, "result", "data", "description", "fields")
        if not isinstance(fields, dict):
            continue
        if any(str(fields[key]) != product_id for key in ("offerId", "productId", "itemId") if key in fields):
            continue
        url = image_url(fields.get("detailUrl"))
        if url and url not in urls:
            urls.append(url)
    return urls[0] if len(urls) == 1 else None


def enrich_detail_images(client, text: str, result):
    """Do not downgrade product results for optional description failures.

    Request-control aborts intentionally propagate, preserving cancellation and
    task deadline semantics. No transport retries or automatic redirects.
    """
    if not result.ok:
        return result
    response = None
    try:
        url = description_url(text, result.product_id)
        if url is None:
            result.warnings.append("detail_images_unavailable")
            return result
        notify(client.request_control, "before_request")
        try:
            response = client.session.get(
                url, allow_redirects=False, stream=True,
                timeout=min(client.timeout, 5),
                headers={"Accept": "text/plain,text/html,application/json", "Referer": result.canonical_url},
            )
            notify(client.request_control, "observe_response", response)
            if response.status_code != 200:
                raise ValueError("description_http_failed")
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > client.max_bytes:
                    raise ValueError("description_too_large")
                chunks.append(chunk)
            images = parse_description(b"".join(chunks).decode("utf-8-sig"))
        except Exception as exc:
            notify(client.request_control, "observe_error", exc)
            raise
        result.detail_images = images
        if not images:
            result.warnings.append("detail_images_unavailable")
    except Exception:
        result.warnings.append("detail_images_fetch_failed")
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    return result
