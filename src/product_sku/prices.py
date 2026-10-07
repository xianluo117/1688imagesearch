"""SKU-only decimal prices. Production contract stays disabled until evidenced."""
import re
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Any

from .models import Sku

SOURCE = "model.tradeModel.skuMap.priceAmount"


@dataclass(frozen=True)
class PriceContract:
    """Internal reviewed evidence, never populated from untrusted page metadata."""
    decimal_places: int
    currency: str
    basis: str


# No reliable unit/currency/meaning evidence was found. Do not infer cents/CNY.
VERIFIED_CONTRACT: PriceContract | None = None


def decimal_price(raw: Any, contract: PriceContract | None) -> str | None:
    if contract is None or type(contract.decimal_places) is not int or not 0 <= contract.decimal_places <= 12:
        return None
    if not re.fullmatch(r"[A-Z]{3}", contract.currency) or not contract.basis:
        return None
    if type(raw) is int:
        text = str(raw)
    elif isinstance(raw, str):
        text = raw
    else:
        return None  # Booleans and binary floats are not exact monetary inputs.
    if not re.fullmatch(r"[0-9]{1,40}(?:\.[0-9]{1,12})?", text):
        return None
    with localcontext() as context:
        context.prec = 64
        amount = Decimal(text).scaleb(-contract.decimal_places)
        if amount <= 0:
            return None
        return format(amount, "f")


class SkuPrices:
    def __init__(self, contract: PriceContract | None = VERIFIED_CONTRACT):
        self.contract = contract
        self.candidates: dict[str, set[str | None]] = {}
        self.present: set[str] = set()

    def add(self, sku_id: str, row: dict) -> None:
        if row.get("priceAmount") is not None:
            self.present.add(sku_id)
        value = decimal_price(row.get("priceAmount"), self.contract)
        self.candidates.setdefault(sku_id, set()).add(value)

    def finish(self, skus: list[Sku]) -> tuple[list[Sku], list[str]]:
        result = []
        warnings = set()
        for sku in skus:
            values = self.candidates.get(sku.sku_id, {None})
            price = next(iter(values)) if len(values) == 1 else None
            if self.contract is None and sku.sku_id in self.present:
                warnings.add("sku_price_unverified")
            elif len(values) > 1:
                warnings.add("conflicting_sku_price")
            elif price is None and sku.sku_id in self.present:
                warnings.add("invalid_sku_price")
            result.append(replace(sku, price=price,
                                  currency=self.contract.currency if price is not None else None,
                                  price_source=SOURCE if price is not None else None,
                                  price_basis=self.contract.basis if price is not None else None))
        return result, sorted(warnings)
