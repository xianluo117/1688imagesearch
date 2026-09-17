"""Opaque upstream specId metadata; never derive it from skuId or attributes."""
import re
from dataclasses import replace

from .models import Sku


class SpecIds:
    def __init__(self):
        self.values: dict[str, set[str | None]] = {}
        self.owners: dict[str, set[str]] = {}
        self.invalid = False

    def add(self, sku_id: str, raw: object) -> None:
        valid = isinstance(raw, str) and re.fullmatch(r"[0-9a-fA-F]{32}", raw) is not None
        value = raw if valid else None
        if raw is not None and not valid:
            self.invalid = True
        self.values.setdefault(sku_id, set()).add(value)
        if value is not None:
            self.owners.setdefault(value, set()).add(sku_id)

    def finish(self, skus: list[Sku]) -> tuple[list[Sku], list[str]]:
        result = []
        warnings = {"invalid_spec_id"} if self.invalid else set()
        for sku in skus:
            values = self.values.get(sku.sku_id, {None})
            value = next(iter(values)) if len(values) == 1 else None
            if len(values) > 1:
                warnings.add("conflicting_spec_id")
            if value is not None and len(self.owners[value]) != 1:
                value = None
                warnings.add("conflicting_spec_id")
            result.append(replace(sku, spec_id=value))
        return result, sorted(warnings)
