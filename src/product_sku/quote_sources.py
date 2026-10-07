"""Bounded original-quote collection from complete, verified business objects."""
from dataclasses import dataclass, field
from typing import Any

from .extraction import MAX_CANDIDATES, MAX_NODES, DecodeLimit

QUOTE_PATH = ("result", "data", "mainPrice", "fields", "finalPriceModel",
              "tradeWithoutPromotion", "skuMapOriginal")


@dataclass
class QuoteSources:
    by_sku: dict[str, list[Any]] = field(default_factory=dict)
    warnings: set[str] = field(default_factory=set)
    containers: int = 0
    rows: int = 0
    valid_rows: int = 0
    amount_types: dict[str, int] = field(default_factory=dict)

    def warnings_for(self, skus) -> list[str]:
        warnings = set(self.warnings)
        ids = {sku.sku_id for sku in skus}
        if ids and self.containers == 0:
            warnings.add("sku_price_source_missing")
        if ids and self.by_sku and not ids.intersection(self.by_sku):
            warnings.add("sku_price_unmatched")
        if any(sku.price is None for sku in skus):
            warnings.add("sku_price_missing")
        return sorted(warnings)


def collect_quotes(verified_roots: list[dict], product_id: str) -> QuoteSources:
    # Lazy import preserves the existing parser helper entry points.
    from .parser import IDENTITY_KEYS, _decode_array, identifier

    if len(verified_roots) > MAX_CANDIDATES:
        raise DecodeLimit("candidate_limit")
    result = QuoteSources()
    for root in verified_roots:
        node = root
        for key in QUOTE_PATH:
            if node is None:
                break
            if not isinstance(node, dict) or any(
                    identifier(node[k]) != product_id for k in IDENTITY_KEYS if k in node):
                result.warnings.add("invalid_sku_price_schema")
                node = None
                break
            node = node.get(key)
        if node is None:
            continue
        node = _decode_array(node)
        if not isinstance(node, list):
            result.warnings.add("invalid_sku_price_schema")
            continue  # Mapping keys have no verified SKU ownership semantics.
        result.containers += 1
        for row in node:
            result.rows += 1
            if result.rows > MAX_NODES:
                raise DecodeLimit("row_limit")
            if (not isinstance(row, dict) or not identifier(row.get("skuId"))
                    or any(identifier(row[k]) != product_id for k in IDENTITY_KEYS if k in row)):
                result.warnings.add("invalid_sku_price_rows")
                continue
            sku_id = identifier(row["skuId"])
            amount = row.get("price")
            result.by_sku.setdefault(sku_id, []).append(amount)
            result.valid_rows += 1
            kind = type(amount).__name__
            result.amount_types[kind] = result.amount_types.get(kind, 0) + 1
    return result
