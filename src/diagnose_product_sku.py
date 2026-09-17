"""Explicit opt-in online diagnosis: one bounded request, memory-only page.

Run from the project root. No API/database initialization or service restart.
Only fixed schema paths, counts, booleans and parser categories are emitted.
"""
import argparse
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from api.config import Settings
from api.cookie_store import CookieStore
from api.database import Database
from product_sku.client import ProductSkuClient
from product_sku.extraction import Page, json_roots
from product_sku.parser import _path, parse_detail
from product_sku.urls import normalize_url

PRODUCT_ID = "898728774563"
URL = f"https://detail.1688.com/offer/{PRODUCT_ID}.html"


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=True))


def inspect_page(text, url):
    page = Page(text)
    emit("page", product_id=PRODUCT_ID, length=len(text), scripts=len(page.scripts),
         blocked=bool(page.blocked), image_meta=bool(page.main_image(PRODUCT_ID)))
    try:
        roots, malformed = json_roots(page)
        emit("extraction", root_count=len(roots), malformed=malformed)
        for root in roots:
            global_data = _path(root, "result", "global", "globalData")
            if not isinstance(global_data, dict):
                continue
            model = _path(global_data, "model")
            scale = _path(model, "detailDescription", "pieceWeightScale")
            rows = _path(scale, "pieceWeightScaleInfo")
            columns = _path(scale, "columnList")
            props = _path(model, "offerDetail", "skuProps")
            gallery = _path(root, "result", "data", "gallery", "fields")
            emit("ownership", parameter_match=_path(global_data, "parametersMap", "offerId") in (PRODUCT_ID, int(PRODUCT_ID)),
                 detail_match=_path(model, "offerDetail", "offerId") in (PRODUCT_ID, int(PRODUCT_ID)),
                 trade_match=_path(model, "tradeModel", "offerId") in (PRODUCT_ID, int(PRODUCT_ID)))
            for label, value in (("rows", rows), ("columns", columns), ("props", props)):
                emit("schema", location=label, candidate_type=type(value).__name__,
                     count=len(value) if isinstance(value, (dict, list)) else None)
                if isinstance(value, list):
                    for item in value[:3]:
                        if isinstance(item, dict):
                            # Explicitly allow-listed schema names; never dynamic keys.
                            allowed = [k for k in item if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", k)]
                            emit("item_schema", location=label, fields=[k for k in allowed if k in item],
                                 field_count=len(item))
            sku_map = _path(model, "tradeModel", "skuMap")
            emit("trade_schema", candidate_type=type(sku_map).__name__, count=len(sku_map) if isinstance(sku_map, (dict, list)) else None)
            if isinstance(sku_map, (dict, list)):
                for item in (list(sku_map.values()) if isinstance(sku_map, dict) else sku_map)[:2]:
                    if isinstance(item, dict):
                        emit("trade_row_schema", fields=[k for k in item if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", k)])
                        for key, child in item.items():
                            if isinstance(child, list):
                                emit("trade_list_schema", field=key, count=len(child), item_type=type(child[0]).__name__ if child else None,
                                     fields=list(child[0]) if child and isinstance(child[0], dict) else [])
            if isinstance(sku_map, list):
                counts = {}
                for row in sku_map:
                    attrs = row.get("specAttrs") if isinstance(row, dict) else None
                    kind = type(attrs).__name__
                    counts[kind] = counts.get(kind, 0) + 1
                emit("spec_attrs_types", counts=counts)
                if sku_map and isinstance(sku_map[0], dict):
                    attrs = sku_map[0].get("specAttrs")
                    if isinstance(attrs, str):
                        try:
                            decoded = json.loads(attrs)
                            emit("spec_attrs_json", candidate_type=type(decoded).__name__, count=len(decoded) if isinstance(decoded, (dict, list)) else None,
                                 fields=list(decoded[0]) if isinstance(decoded, list) and decoded and isinstance(decoded[0], dict) else [])
                        except ValueError:
                            emit("spec_attrs_json", candidate_type="not_json")
                        if isinstance(props, list):
                            masked = attrs
                            labels = []
                            for prop in props:
                                labels.append(prop.get("prop", ""))
                                labels.extend(v.get("name", "") for v in prop.get("value", []) if isinstance(v, dict))
                            for label in sorted(set(labels), key=len, reverse=True):
                                if label:
                                    masked = masked.replace(label, "X")
                            emit("attribute_shape", shape="".join(c if c in "X:;|,> &=-_" else "?" for c in masked)[:120])
                        for delimiter in (chr(38) + "gt;", chr(38) + "lt;", chr(38) + "amp;", chr(38) + "nbsp;"):
                            matched = 0
                            for row in sku_map:
                                parts = row.get("specAttrs", "").split(delimiter)
                                if isinstance(props, list) and len(parts) == len(props) and all(
                                    part in {v.get("name") for v in prop.get("value", []) if isinstance(v, dict)}
                                    for part, prop in zip(parts, props)):
                                    matched += 1
                            emit("position_mapping", delimiter=delimiter, matched_rows=matched)
                    elif isinstance(attrs, dict):
                        emit("spec_attrs_object", count=len(attrs), keys_match_prop_names=isinstance(props, list) and set(attrs) == {p.get("prop") for p in props})
            image = _path(gallery, "mainImage")
            if isinstance(image, list) and image:
                emit("image_item_schema", candidate_type=type(image[0]).__name__, fields=list(image[0]) if isinstance(image[0], dict) else [])
            if isinstance(props, list):
                for prop in props[:3]:
                    values = prop.get("value") if isinstance(prop, dict) else None
                    emit("prop_values_schema", candidate_type=type(values).__name__, count=len(values) if isinstance(values, list) else None,
                         fields=list(values[0]) if isinstance(values, list) and values and isinstance(values[0], dict) else [])
            emit("image_schema", owner_match=_path(gallery, "offerId") in (PRODUCT_ID, int(PRODUCT_ID)),
                 candidate_type=type(image).__name__,
                 fields=[k for k in ("url", "fullPath", "imageUrl", "src") if isinstance(image, dict) and k in image])
    except Exception as exc:
        emit("extraction_failure", category=type(exc).__name__)
    result = parse_detail(text, url)
    emit("result", status=result.status, reason=result.reason, sku_count=len(result.skus),
         main_image=bool(result.main_image), warnings=result.warnings,
         positions=sorted({s.position for row in result.skus for s in row.specifications}),
         named_specifications=sum(s.name is not None for row in result.skus for s in row.specifications))
    return result


async def run():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    database = Database(settings.database_path)
    if not database.path.is_file():
        emit("failure", category="database_missing")
        return
    _, records = await CookieStore(database, settings.cookie_encryption_key).load_active()
    with ProductSkuClient(cookie_records=records, timeout=min(settings.sku_http_timeout, 20), network_retries=0) as client:
        original_get = client.session.get
        def get(*args, **kwargs):
            response = original_get(*args, **kwargs)
            emit("http", status=response.status_code)
            return response
        with patch.object(client.session, "get", get), patch("product_sku.client.parse_detail", inspect_page):
            result = client.fetch(URL)
        emit("completed", status=result.status, reason=result.reason, sku_count=len(result.skus))


if __name__ == "__main__":
    try:
        arguments = argparse.ArgumentParser(description="Memory-only, redacted SKU diagnosis")
        arguments.add_argument("--url", default=URL)
        options = arguments.parse_args()
        PRODUCT_ID, URL = normalize_url(options.url)
        asyncio.run(run())
    except Exception as exc:
        emit("failure", category=type(exc).__name__)
        raise SystemExit(1) from None
