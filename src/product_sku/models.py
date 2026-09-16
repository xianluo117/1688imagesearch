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

    @property
    def ok(self) -> bool:
        return self.status in {"success", "partial_success"} and bool(self.skus)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["sku_count"] = len(self.skus)
        return result
