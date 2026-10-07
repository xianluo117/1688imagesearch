"""One-request, allow-listed price evidence diagnosis; page stays in memory."""
import asyncio
from unittest.mock import patch

import diagnose_product_sku as diagnosis
from product_sku.extraction import Page, json_roots
from product_sku.parser import _arrays, parse_detail

PRODUCT_ID = "844515661443"
URL = f"https://detail.1688.com/offer/{PRODUCT_ID}.html"


def inspect_page(text, url):
    roots, malformed = json_roots(Page(text))
    models = []
    _arrays(roots, PRODUCT_ID, models)
    diagnosis.emit("price_evidence", verified_models=len(models), malformed=malformed)
    # No arbitrary keys/values, account data, URLs or HTML are printed.
    fields = ("priceAmount", "currency", "currencyCode", "priceUnit", "amountUnit",
              "priceScale", "priceType", "priceDescription")
    for model in models:
        trade = model["tradeModel"]
        rows = trade.get("skuMap")
        rows = rows if isinstance(rows, list) else []
        diagnosis.emit("price_schema", rows=len(rows),
                       positive_integer_amounts=sum(type(row.get("priceAmount")) is int and row["priceAmount"] > 0
                                                    for row in rows if isinstance(row, dict)),
                       trade_metadata_present=[key for key in fields[1:] if key in trade],
                       detail_metadata_present=[key for key in fields[1:] if key in model["offerDetail"]],
                       row_metadata_counts={key: sum(key in row for row in rows if isinstance(row, dict))
                                            for key in fields[1:]})
    result = parse_detail(text, url)
    diagnosis.emit("result", status=result.status, reason=result.reason, sku_count=len(result.skus),
                   sizes=len(getattr(result, "sizes", [])),
                   color_sizes=len(getattr(result, "color_sizes", [])),
                   prices_returned=sum(getattr(sku, "price", None) is not None for sku in result.skus))
    return result


if __name__ == "__main__":
    diagnosis.PRODUCT_ID, diagnosis.URL = PRODUCT_ID, URL
    # Client already disables retries. Enforce exactly one session GET including redirects.
    from product_sku.client import ProductSkuClient
    original_fetch = ProductSkuClient.fetch

    def fetch_once(client, url):
        original_get = client.session.get
        calls = 0

        def get(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("diagnostic_request_limit")
            return original_get(*args, **kwargs)

        with patch.object(client.session, "get", get):
            return original_fetch(client, url)

    try:
        with patch.object(diagnosis, "inspect_page", inspect_page), patch.object(ProductSkuClient, "fetch", fetch_once):
            asyncio.run(diagnosis.run())
    except Exception as exc:
        diagnosis.emit("failure", category=type(exc).__name__)
        raise SystemExit(1) from None
