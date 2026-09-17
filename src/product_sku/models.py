"""Independent detail-page SKU result types; no database or image-search integration."""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Specification:
    position: int
    field: str
    value: str | None
    name: str | None = None


@dataclass(frozen=True)
class Sku:
    sku_id: str
    specifications: list[Specification]
    spec_id: str | None = None


@dataclass(frozen=True)
class SpecificationImage:
    position: int
    field: str
    name: str | None
    value: str | None
    image_url: str | None = None


@dataclass
class SkuResult:
    product_id: str
    canonical_url: str
    status: str
    reason: str
    source: str = "detail_html"
    main_image: str | None = None
    skus: list[Sku] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completeness: str = "unknown"
    seller_user_id: str | None = None
    seller_member_id: str | None = None
    specification_images: list[SpecificationImage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"success", "partial_success"} and bool(self.skus)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["sku_count"] = len(self.skus)
        return result
