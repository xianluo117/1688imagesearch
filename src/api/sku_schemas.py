"""Typed HTTP contract for the independent SKU query."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from product_sku.models import Sku, SpecificationImage


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
