import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")
from dotenv import load_dotenv
from api.config import Settings
from api.cookie_store import CookieStore
from api.database import Database
from api.sku_v2_converter import convert_result
from product_sku.client import ProductSkuClient


async def main():
    load_dotenv(Path(".env"), override=False)
    settings = Settings.from_env()
    _, records = await CookieStore(Database(settings.database_path), settings.cookie_encryption_key).load_active()
    with ProductSkuClient(cookie_records=records, network_retries=0, timeout=min(settings.sku_http_timeout, 20)) as client:
        result = client.fetch("https://detail.1688.com/offer/898728774563.html")
    v1 = result.to_dict()
    v2 = convert_result(result).model_dump()
    prices = [sku["price"] for sku in v2["data"]["skus"]]
    print({
        "http_status": 200,
        "status": result.status,
        "sku_count": len(result.skus),
        "v1_nonempty_amounts": sum(sku["price"] is not None for sku in v1["skus"]),
        "v1_nonempty_currency": sum(sku["currency"] is not None for sku in v1["skus"]),
        "v1_nonempty_basis": sum(sku["price_basis"] is not None for sku in v1["skus"]),
        "v2_available": sum(price["status"] == "available" for price in prices),
        "v2_sample_price": prices[0],
    })


if __name__ == "__main__":
    asyncio.run(main())
