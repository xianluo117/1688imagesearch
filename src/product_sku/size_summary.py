"""Name-based summaries of final SKUs; no positional or value inference."""
from .models import ColorSizes, SizeDimension, Sku, Specification

SIZE_NAMES = frozenset({"尺码", "尺寸", "大小", "size"})
COLOR_NAMES = frozenset({"颜色", "色彩", "color", "colour"})


def _kind(name: str | None) -> str | None:
    normalized = name.strip().casefold() if isinstance(name, str) else ""
    if normalized in SIZE_NAMES:
        return "size"
    if normalized in COLOR_NAMES:
        return "color"
    return None


def summarize_sizes(skus: list[Sku]) -> tuple[list[SizeDimension], list[ColorSizes]]:
    dimensions: dict[tuple[int, str], set[str | None]] = {}
    for sku in skus:
        for spec in sku.specifications:
            dimensions.setdefault((spec.position, spec.field), set()).add(spec.name)
    # An inconsistent or missing name cannot establish a dimension's meaning.
    recognized = {key: (next(iter(names)), _kind(next(iter(names))))
                  for key, names in dimensions.items() if len(names) == 1 and None not in names}
    sizes: dict[tuple[int, str, str], list[str]] = {}
    colors: dict[tuple[int, str, str], None] = {}
    for sku in skus:
        for spec in sku.specifications:
            name, kind = recognized.get((spec.position, spec.field), (None, None))
            if kind == "size" and isinstance(spec.value, str) and spec.value.strip():
                values = sizes.setdefault((spec.position, spec.field, name), [])
                if spec.value not in values:
                    values.append(spec.value)
            elif kind == "color":
                colors[(spec.position, spec.field, name)] = None
    summaries = [SizeDimension(*key, values) for key, values in sizes.items()]
    # Multiple color/size dimensions retain separate summaries but no guessed association.
    if sum(kind == "size" for _, kind in recognized.values()) != 1 or len(sizes) != 1 or len(colors) != 1:
        return summaries, []
    size_key, color_key = next(iter(sizes)), next(iter(colors))
    linked: dict[Specification, list[str]] = {}
    for sku in skus:
        color = [s for s in sku.specifications if (s.position, s.field, s.name) == color_key]
        size = [s for s in sku.specifications if (s.position, s.field, s.name) == size_key]
        if len(color) != 1 or len(size) != 1:
            continue
        if not all(isinstance(s.value, str) and s.value.strip() for s in (color[0], size[0])):
            continue
        values = linked.setdefault(color[0], [])
        if size[0].value not in values:
            values.append(size[0].value)
    return summaries, [ColorSizes(color, *size_key, values) for color, values in linked.items()]
