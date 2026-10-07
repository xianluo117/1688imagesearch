"""Fixed-schema diagnostics; no dynamic page keys or secret values are emitted."""
from collections import Counter

from .parser import IDENTITY_KEYS, _decode_array, identifier
from .prices import VERIFIED_CONTRACT, decimal_price

MAIN = ("result", "data", "mainPrice", "fields")
MODEL = ("result", "global", "globalData", "model")
# Candidate paths are diagnostic only, not new production price contracts.
CONTAINERS = (
    ("skuMapOriginal",), ("skuMap",), ("priceRangeList",),
    ("priceRanges",), ("priceList",), ("rangePrices",),
)
TRADES = ("tradeWithoutPromotion", "tradeWithPromotion", "tradeWithDiscount",
          "tradePromotion", "tradeOriginal")
AMOUNTS = ("price", "originalPrice", "discountPrice", "minPrice", "maxPrice",
           "priceAmount", "promotionSku")
METADATA = ("skuPriceType", "priceDisplayType", "originPriceType", "unit",
            "priceType", "currency", "currencyCode", "priceScale")
ENUMS = {"skuPrice", "rangePrice", "stepPrice", "singlePrice", "fixedPrice",
         "uniformPrice", "samePrice", "price", "CNY", "RMB", "元", "件",
         "套", "个", "双", "箱", "条", "批", "阶梯价", "区间价"}


def locate(root, path):
    node = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


def owned(root, path, product_id):
    node = root
    for key in path:
        if not isinstance(node, dict) or any(
                identifier(node[k]) != product_id for k in IDENTITY_KEYS if k in node):
            return False
        node = node.get(key)
    return not isinstance(node, dict) or not any(
        identifier(node[k]) != product_id for k in IDENTITY_KEYS if k in node)


def field_stats(rows, key, samples=False):
    dictionaries = [row for row in rows if isinstance(row, dict)]
    values = [row[key] for row in dictionaries if key in row]
    result = {"present": len(values), "missing": len(dictionaries) - len(values),
              "null": sum(value is None for value in values),
              "types": dict(Counter(type(value).__name__ for value in values))}
    # priceAmount is historically not money; never sample or scale it.
    if samples and key not in {"priceAmount", "promotionSku"}:
        amounts = [decimal_price(value, VERIFIED_CONTRACT) for value in values]
        result["amount_samples"] = list(dict.fromkeys(a for a in amounts if a is not None))[:3]
    return result


def inspect_fixed(root, product_id, ids, emit, samples=False):
    paths = [MAIN, MAIN + ("finalPriceModel",), MODEL + ("offerDetail",),
             MODEL + ("tradeModel",)]
    paths += [MAIN + ("finalPriceModel", trade) for trade in TRADES]
    paths += [MAIN + (trade,) for trade in TRADES]
    for path in paths:
        present, node = locate(root, path)
        safe = owned(root, path, product_id)
        emit("fixed_price_node", path=".".join(path), present=present,
             kind=type(node).__name__, ownership_valid=safe)
        if not isinstance(node, dict) or not safe:
            continue
        metadata = {}
        for key in METADATA:
            if key not in node:
                metadata[key] = {"present": False}
                continue
            value = node[key]
            metadata[key] = {"present": True, "kind": type(value).__name__}
            if value is None or type(value) in (int, bool) or isinstance(value, str) and value in ENUMS:
                metadata[key]["value"] = value
            else:
                metadata[key]["value"] = "unrecognized_redacted"
        emit("price_modes", path=".".join(path), fields=metadata)
        emit("fixed_scalar_prices", path=".".join(path),
             fields={key: field_stats([node], key, samples) for key in AMOUNTS})
        # A dictionary price is inspected only via this explicit field whitelist.
        for key in ("price", "priceModel", "priceRange", "skuPrice", "promotionSku"):
            if isinstance(node.get(key), dict):
                emit("fixed_nested_price", path=".".join(path + (key,)),
                     fields={field: field_stats([node[key]], field, samples)
                             for field in AMOUNTS + ("amount", "begin", "end", "min", "max")})
        for suffix in CONTAINERS:
            candidate = path + suffix
            exists, raw = locate(root, candidate)
            rows = _decode_array(raw) if exists else None
            row_list = rows if isinstance(rows, list) else []
            valid = [row for row in row_list if isinstance(row, dict)
                     and identifier(row.get("skuId")) is not None
                     and not any(identifier(row[k]) != product_id for k in IDENTITY_KEYS if k in row)]
            row_ids = {identifier(row["skuId"]) for row in valid}
            emit("fixed_quote_container", path=".".join(candidate), present=exists,
                 kind=type(rows).__name__, rows=len(row_list),
                 valid_sku_rows=len(valid), unique_sku_ids=len(row_ids),
                 matched=len(ids & row_ids), unmatched=len(row_ids - ids),
                 fields={key: field_stats(row_list, key, samples) for key in AMOUNTS})
            for key in ("price", "discountPrice", "promotionSku"):
                nested = [row[key] for row in row_list if isinstance(row, dict)
                          and isinstance(row.get(key), dict)]
                if nested:
                    emit("fixed_row_nested_price", path=".".join(candidate + (key,)), rows=len(nested),
                         fields={field: field_stats(nested, field, samples)
                                 for field in ("price", "amount", "originalPrice", "discountPrice")})
