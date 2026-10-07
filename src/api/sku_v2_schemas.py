"""Independent, fully typed v2 response contract; v1 request stays shared."""
from typing import Literal

from pydantic import BaseModel


class ProductV2(BaseModel):
    id: str
    url: str
    main_image: str | None


class SellerV2(BaseModel):
    user_id: str | None
    member_id: str | None


class OptionV2(BaseModel):
    option_id: str
    value: str | None
    image_url: str | None


class DimensionV2(BaseModel):
    dimension_id: str
    position: int
    name: str | None
    role: Literal["color", "size", "unknown"]
    options: list[OptionV2]


class SizeDimensionV2(BaseModel):
    dimension_id: str
    name: str
    values: list[str]
    option_ids: list[str]


class ColorSizesV2(BaseModel):
    color_option_id: str
    color: str
    size_dimension_id: str
    sizes: list[str]
    size_option_ids: list[str]


class SizeSummaryV2(BaseModel):
    dimensions: list[SizeDimensionV2]
    by_color: list[ColorSizesV2]


class PriceV2(BaseModel):
    amount: str | None
    currency: str | None
    status: Literal["available", "unavailable"]
    source: str | None
    basis: str | None


class SkuV2(BaseModel):
    sku_id: str
    spec_id: str | None
    option_ids: list[str]
    price: PriceV2


class DataV2(BaseModel):
    product: ProductV2
    seller: SellerV2
    specifications: list[DimensionV2]
    size_summary: SizeSummaryV2
    skus: list[SkuV2]


class MetaV2(BaseModel):
    schema_version: Literal["2"] = "2"
    source: Literal["detail_html"]
    sku_count: int
    completeness: Literal["unknown", "complete", "partial"]
    reason: str
    warnings: list[str]


class ProductSkuResponseV2(BaseModel):
    status: Literal[
        "success", "partial_success", "access_restricted", "login_required",
        "source_not_applicable", "parse_failed", "network_failed",
    ]
    data: DataV2
    meta: MetaV2
