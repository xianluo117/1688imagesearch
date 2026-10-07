"""Memory-only allow-listed price/schema evidence; never emit page or secrets."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from product_sku.extraction import Page, json_roots
from product_sku.parser import _arrays, parse_detail


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


def inspect(text, url, product_id, emit):
    roots, malformed = json_roots(Page(text))
    models = []
    _arrays(roots, product_id, models)
    emit("ownership", verified_models=len(models), malformed=malformed)
    emit("currency_markup", cny_meta=bool(re.search(r'price:currency[^>]{0,100}CNY', text)),
         yuan_numeric=bool(re.search(r'(?:¥|￥|&yen;)[^0-9<>]{0,10}[0-9]+[.][0-9]{2}', text)))
    for match in re.finditer(r'[@]alife/[A-Za-z0-9_-]+|upkg/[A-Za-z0-9_./-]+', text):
        emit("package_reference", value=match.group(0))
    def modules(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and re.fullmatch(r"[@A-Za-z0-9_./:-]{1,180}", child) and re.search(r"price|sku|upkg", child, re.I):
                    emit("module_reference", field=key if re.fullmatch(r"[A-Za-z_]+", key) else "module", value=child)
                elif isinstance(child, (dict, list)):
                    modules(child)
        elif isinstance(value, list):
            for child in value:
                modules(child)
    modules(roots)

    def walk(value, path, depth=0):
        if depth > 20:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", key):
                    continue
                location = path + "." + key
                if re.search(r"price|currency|money|amount", key, re.I):
                    emit("price_field", path=location, kind=type(child).__name__,
                         fields=[k for k in child if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", k)] if isinstance(child, dict) else None,
                         numeric=str(child) if type(child) in (int, float) or isinstance(child, str) and re.fullmatch(r"[0-9.]{1,40}|CNY|RMB|元|分|¥|￥|skuPrice|rangePrice", child) else None)
                walk(child, location, depth + 1)
        elif isinstance(value, list):
            for child in value[:2]:
                walk(child, path + "[]", depth + 1)

    for root in roots:
        from product_sku.parser import _verified_model
        if isinstance(root, dict) and _verified_model(root, product_id) is not None:
            walk(root.get("result", {}).get("data", {}), "result.data")
    for model in models:
        walk(model, "model")
        for row in model.get("tradeModel", {}).get("skuMap", [])[:3]:
            emit("sku_sample", sku_id=str(row.get("skuId")), specifications=row.get("specAttrs"), raw_amount=row.get("priceAmount"))
    # Only public static bundle addresses, no query or account URL.
    emit("public_assets", urls=Assets(text).urls[:20])
    urls = sorted(set(re.findall(r"(?:https:)?//[A-Za-z0-9.-]*alicdn\.com/[A-Za-z0-9_./@-]+", text)))
    emit("static_scripts", urls=[u for u in urls if u.endswith('.js')][:40])
    emit("loader_names", names=sorted(set(re.findall(r'"([A-Za-z_][A-Za-z_0-9]*)"\\s*:', text)))[:100])
    emit("all_public_assets", urls=[u for u in urls if "upkg" in u or "price" in u.lower() or "od-" in u][:40])
    for script in Page(text).scripts:
        if "pcOfferDetailDscPrice25" in script or "od-pc" in script:
            for match in re.finditer(r"pcOfferDetailDscPrice25|@alife/[A-Za-z0-9_-]+|https://g[.]alicdn[.]com/[A-Za-z0-9_./@-]+", script):
                emit("public_module", value=match.group(0))
    for root in roots:
        from product_sku.parser import _path, _verified_model
        if isinstance(root, dict) and _verified_model(root, product_id) is not None:
            data = _path(root, "result", "data") or {}
            for name in ("Root", "mainPrice"):
                fields = _path(data, name, "fields") or {}
                emit("component_schema", component=name, fields=list(fields),
                     unit=fields.get("unit") if fields.get("unit") in ("元", "¥", "￥", "CNY", "RMB", "件", "条") else None,
                     origin_price_type=fields.get("originPriceType") if fields.get("originPriceType") in ("skuPrice", "rangePrice") else None)
                component = data.get(name, {})
                def public_urls(value):
                    if isinstance(value, str) and value.startswith(("https://g.alicdn.com/", "//g.alicdn.com/")) and "?" not in value:
                        emit("component_asset", component=name, url=value)
                    elif isinstance(value, dict):
                        for child in value.values():
                            public_urls(child)
                    elif isinstance(value, list):
                        for child in value[:5]:
                            public_urls(child)
                public_urls(component.get("meta", {}))
                meta = component.get("meta", {})
                emit("component_module", component=name, fields=list(meta) if isinstance(meta, dict) else [],
                     modules={k:v for k,v in meta.items() if isinstance(v,str) and re.fullmatch(r"[@A-Za-z0-9_./-]{1,160}",v)} if isinstance(meta,dict) else {})
            rows = _path(data, "mainPrice", "fields", "finalPriceModel", "tradeWithoutPromotion", "skuMapOriginal") or []
            emit("original_quote_summary", count=len(rows), priced=sum(isinstance(r, dict) and r.get("price") is not None for r in rows))
            for row in rows[:3]:
                emit("original_quote_sample", fields=list(row), sku_id=str(row.get("skuId")), specifications=row.get("specAttrs"), price=row.get("price"), discount_price=row.get("discountPrice"), price_amount=row.get("priceAmount"))
    result = parse_detail(text, url)
    emit("parsed", sku_count=len(result.skus), prices=sum(s.price is not None for s in result.skus))
    return result
