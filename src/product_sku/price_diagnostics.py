"""Memory-only allow-listed price/schema evidence; never emit page or secrets."""
from html.parser import HTMLParser
from urllib.parse import urlsplit

from product_sku.extraction import Page, json_roots
from product_sku.parser import _arrays, parse_detail
from product_sku.quote_sources import QUOTE_PATH, collect_quotes


class Assets(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.urls = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        url = attrs.get("src", "") if tag == "script" else ""
        if url.startswith("//"):
            url = "https:" + url
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname in {"g.alicdn.com", "assets.alicdn.com"} and not parts.query:
            self.urls.append(url)


def inspect(text, url, product_id, emit, *, samples=False):
    """Report fixed-path types/counts only, using production ownership rules."""
    roots, malformed = json_roots(Page(text))
    models, verified_roots = [], []
    _arrays(roots, product_id, models, verified_roots=verified_roots)
    quotes = collect_quotes(verified_roots, product_id)
    emit("ownership", verified_models=len(models), malformed=malformed)
    for root in verified_roots:
        node = root
        for index, key in enumerate(QUOTE_PATH):
            node = node.get(key) if isinstance(node, dict) else None
            emit("quote_path", path=".".join(QUOTE_PATH[:index + 1]), kind=type(node).__name__)
    result = parse_detail(text, url)
    ids = {sku.sku_id for sku in result.skus}
    emit("original_quote_summary", containers=quotes.containers, count=quotes.rows,
         valid_rows=quotes.valid_rows, amount_types=quotes.amount_types,
         matched=len(ids.intersection(quotes.by_sku)),
         unmatched=len(set(quotes.by_sku).difference(ids)))
    from .price_evidence import inspect_fixed
    for root in verified_roots:
        inspect_fixed(root, product_id, ids, emit, samples)
    emit("parsed", sku_count=len(result.skus), prices=sum(s.price is not None for s in result.skus),
         warnings=result.warnings)
    return result
