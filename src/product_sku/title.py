"""Product title extraction from scoped detail data and Open Graph metadata."""
from .extraction import Page
from .urls import normalize_url


def product_title(models: list[dict], sellers: list[dict], page: Page,
                  product_id: str) -> str | None:
    """Use current-product sources only; never search arbitrary nested titles."""
    candidates = set()
    sources = [model.get("offerDetail", {}) for model in models] + sellers
    for source in sources:
        for key in ("subject", "title", "offerTitle"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip())
    if candidates:
        return next(iter(candidates)) if len(candidates) == 1 else None

    urls = page.meta.get("og:url", set())
    if len(urls) != 1:
        return None
    try:
        if normalize_url(next(iter(urls)))[0] != product_id:
            return None
    except ValueError:
        return None
    titles = {value.strip() for value in page.meta.get("og:title", set()) if value.strip()}
    return next(iter(titles)) if len(titles) == 1 else None
