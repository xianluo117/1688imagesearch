from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .errors import ProtocolError


@dataclass(frozen=True)
class Product:
    offer_id: str
    link_url: str | None
    image_url: str | None
    title: str | None
    price: str | None
    sale_quantity: str | int | float | None
    seller_login_id: str | None
    seller_name: str | None
    is_ad: bool
    raw: dict[str, Any] | None = None

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_raw:
            result.pop("raw", None)
        return result


@dataclass(frozen=True)
class ParsedPage:
    products: list[Product]
    has_more: bool | None
    found: int | None
    offer_node: dict[str, Any]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_offer_node(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        offer = value.get("OFFER")
        if isinstance(offer, dict) and isinstance(offer.get("items"), list):
            return offer
        if isinstance(value.get("items"), list) and any(
            key in value for key in ("hasMore", "found", "total", "pageSize")
        ):
            return value
        for child in value.values():
            found = _find_offer_node(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_offer_node(child)
            if found is not None:
                return found
    return None


def _price(item: dict[str, Any]) -> str | None:
    price_info = _as_dict(item.get("priceInfo"))
    value = _first(price_info, "price", "priceText", "promotionPrice")
    if value is None:
        value = _first(item, "price", "priceText")
    return None if value is None else str(value)


def normalize_product(item: dict[str, Any], *, include_raw: bool = False) -> Product | None:
    data = _as_dict(item.get("data")) or item
    offer_id = _first(data, "offerId", "id", "itemId")
    if offer_id is None:
        return None

    shop_addition = _as_dict(data.get("shopAddition"))
    seller = _as_dict(data.get("seller"))
    is_ad = bool(_to_bool(data.get("isAd")) or _to_bool(data.get("fmAd")) or _to_bool(item.get("isAd")))
    return Product(
        offer_id=str(offer_id),
        link_url=_first(data, "linkUrl", "detailUrl", "offerUrl"),
        image_url=_first(data, "offerPicUrl", "imageUrl", "picUrl"),
        title=_first(data, "title", "subject", "offerTitle"),
        price=_price(data),
        sale_quantity=_first(data, "saleQuantity", "saleCount", "soldCount"),
        seller_login_id=_first(data, "loginId", "sellerLoginId") or _first(seller, "loginId"),
        seller_name=_first(shop_addition, "text", "name") or _first(seller, "companyName", "name"),
        is_ad=is_ad,
        raw=item if include_raw else None,
    )


def parse_page(payload: dict[str, Any], *, include_raw: bool = False) -> ParsedPage:
    offer_node = _find_offer_node(payload)
    if offer_node is None:
        raise ProtocolError("图搜响应中未找到 OFFER.items")

    products = []
    for item in offer_node.get("items", []):
        if not isinstance(item, dict):
            continue
        product = normalize_product(item, include_raw=include_raw)
        if product is not None:
            products.append(product)

    return ParsedPage(
        products=products,
        has_more=_to_bool(_first(offer_node, "hasMore", "has_more")),
        found=_to_int(_first(offer_node, "found", "total", "totalCount")),
        offer_node=offer_node,
    )


def deduplicate_products(products: Iterable[Product]) -> list[Product]:
    seen: set[str] = set()
    result: list[Product] = []
    for product in products:
        if product.offer_id in seen:
            continue
        seen.add(product.offer_id)
        result.append(product)
    return result
