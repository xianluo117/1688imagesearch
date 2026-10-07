"""Bounded current-session SKU selector diagnosis, memory-only redacted output."""
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from api.config import Settings
from api.cookie_store import CookieStore
from api.database import Database
from diagnose_product_sku import emit
from image_search.mtop import MtopClient, MtopRequest
from product_sku.client import ProductSkuClient


async def run():
    logging.disable(logging.CRITICAL)
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    settings = Settings.from_env()
    _, records = await CookieStore(Database(settings.database_path), settings.cookie_encryption_key).load_active()
    with ProductSkuClient(cookie_records=records, network_retries=0) as detail:
        client = MtopClient(detail.session, network_retries=0, timeout=20)
        detail.session.headers.update({"Origin": "https://detail.1688.com", "Referer": "https://detail.1688.com/offer/844515661443.html"})
        original_post = detail.session.post
        calls = 0
        def post(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise ValueError("request_limit")
            response = original_post(*args, **kwargs)
            emit("selector_http", status=response.status_code, attempt=calls)
            return response
        detail.session.post = post
        payload = client.request(MtopRequest("mtop.1688.wosc.queryofferskuselectormodel", "1.0", {"offerId": "844515661443", "useCase": "1688detail", "bizScene": "pcod", "urlParam": ""}, data_type="json"))
        def walk(value, path, depth=0):
            if depth > 12:
                return
            if isinstance(value, dict):
                emit("selector_schema", path=path, fields=[k for k in value if k in {"data", "result", "model", "offerId", "skuSelectorBizModel", "originalSkuInfoMap", "skuModel", "skuInfoMap", "skuProps", "orderParamModel", "orderParam", "skuParam", "skuPriceType", "price", "discountPrice", "priceAmount", "skuId", "specId", "specAttrs", "currency", "currencyCode", "tempModel", "offerUnit"}])
                for key in ("offerId", "skuPriceType", "price", "discountPrice", "priceAmount", "skuId", "specAttrs", "currency", "currencyCode", "offerUnit"):
                    if key in value and type(value[key]) in (str, int, float):
                        emit("selector_value", path=path + "." + key, value=value[key])
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        walk(child, path + (".[sku]" if key.isdigit() or ">" in key else "." + key), depth + 1)
            elif isinstance(value, list):
                for child in value[:2]:
                    walk(child, path + "[]", depth + 1)
        walk(payload.get("data", {}), "data")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        emit("selector_failure", category=type(exc).__name__)
        raise SystemExit(1) from None
