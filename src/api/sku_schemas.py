"""Typed HTTP contract for the independent SKU query."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from product_sku.models import ColorSizes, SizeDimension, Sku, SpecificationImage

from .sku_price_presentation import present_price


class ProductSkuRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_url: str = Field(strict=True, min_length=1, max_length=4096)


class ProductSkuResponse(BaseModel):
    product_id: str
    canonical_url: str
    status: Literal[
        "success", "partial_success", "access_restricted", "login_required",
        "source_not_applicable", "parse_failed", "network_failed",
    ]
    reason: str
    source: Literal["detail_html"]
    main_image: str | None
    skus: list[Sku]
    warnings: list[str]
    completeness: Literal["unknown", "complete", "partial"]
    sku_count: int
    seller_user_id: str | None = None
    seller_member_id: str | None = None
    specification_images: list[SpecificationImage] = Field(default_factory=list)
    sizes: list[SizeDimension] = Field(default_factory=list)
    color_sizes: list[ColorSizes] = Field(default_factory=list)

    @field_serializer("skus", mode="wrap")
    def serialize_sku_prices(self, skus, handler):
        """Change only HTTP output, not internal Sku values or its schema."""
        rows = handler(skus)
        for row in rows:
            if "price" in row:
                row["price"] = present_price(row["price"])
        return rows
