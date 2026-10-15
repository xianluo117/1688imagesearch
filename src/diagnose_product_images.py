"""Inspect product image source shapes using the existing opt-in diagnosis runner."""
import asyncio
import json
import re

import diagnose_product_sku as diagnosis
from product_sku.extraction import Page, json_roots
from product_sku.parser import _path, _verified_model, parse_detail


def shape(value, depth=0):
    if depth > 7:
        return type(value).__name__
    if isinstance(value, dict):
        return {key: shape(child, depth + 1) for key, child in value.items()}
    if isinstance(value, list):
        return {"count": len(value), "items": [shape(child, depth + 1) for child in value[:2]]}
    if isinstance(value, str):
        if value.startswith(("https://", "http://", "//")):
            return value
        return {"type": "string", "length": len(value), "html_images": len(re.findall(r"<img\b", value, re.I))}
    return type(value).__name__


def inspect_images(text, url):
    page = Page(text)
    roots, _ = json_roots(page)
    for root in roots:
        model = _verified_model(root, diagnosis.PRODUCT_ID) if isinstance(root, dict) else None
        if model is None:
            continue
        data = _path(root, "result", "data")
        print(json.dumps({"model_keys": list(model), "component_keys": list(data) if isinstance(data, dict) else []}, ensure_ascii=True))
        print(json.dumps({"gallery": shape(_path(data, "gallery")), "detailDescription": shape(model.get("detailDescription"))}, ensure_ascii=True))
        if isinstance(data, dict):
            for key, value in data.items():
                if re.search(r"desc|detail|image", key, re.I):
                    print(json.dumps({"component": key, "shape": shape(value)}, ensure_ascii=True))
    result = parse_detail(text, url)
    from api.sku_schemas import ProductSkuResponse
    from api.sku_service import restore_result
    from api.sku_v2_converter import convert_result

    restored = restore_result(result.to_dict())
    v1 = ProductSkuResponse.model_validate(restored.to_dict())
    v2 = convert_result(restored)
    print(json.dumps({"main_image_count": len(result.main_images),
                      "v1_main_images": v1.main_images,
                      "v2_main_image_count": len(v2.data.product.main_images),
                      "roundtrip_equal": result.main_images == v1.main_images == v2.data.product.main_images},
                     ensure_ascii=True))
    return result


def inspect_description(url):
    from curl_cffi import requests
    from html.parser import HTMLParser

    response = requests.get(url, impersonate="chrome", timeout=20)
    print("status:", response.status_code)
    print("content_type:", response.headers.get("Content-Type"))
    print("length:", len(response.content))
    print("prefix:", repr(response.text[:700]))
    images = []
    parser = HTMLParser()
    parser.handle_starttag = lambda tag, attrs: images.append(dict(attrs)) if tag == "img" else None
    match = re.match(r"\s*var\s+offer_details\s*=\s*", response.text)
    content = json.JSONDecoder().raw_decode(response.text[match.end():])[0]["content"] if match else response.text
    parser.feed(content)
    print("image_count:", len(images))
    urls = list(dict.fromkeys(image.get("src") for image in images if image.get("src")))
    print("unique_image_count:", len(urls))
    print(json.dumps(images[:3], ensure_ascii=True))


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--description-url":
        inspect_description(sys.argv[2])
    else:
        diagnosis.inspect_page = inspect_images
        asyncio.run(diagnosis.run())
