"""Independent 1688 detail HTML SKU extraction."""
from .models import Sku, SkuResult, Specification
from .parser import parse_detail

__all__ = ["Sku", "SkuResult", "Specification", "parse_detail"]
