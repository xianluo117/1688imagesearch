"""Name-based summaries of final SKUs; no positional or value inference."""
from .models import ColorSizes, SizeDimension, Sku, Specification
from .size_range import final_size_range

SIZE_NAMES = frozenset({"尺码", "尺寸", "大小", "size"})
COLOR_NAMES = frozenset({"颜色", "色彩", "color", "colour"})


def _kind(name: str | None) -> str | None:
    normalized = name.strip().casefold() if isinstance(name, str) else ""
    if normalized in SIZE_NAMES:
        return "size"
    if normalized in COLOR_NAMES:
        return "color"
    return None


def _raw_sizes(skus: list[Sku]) -> tuple[list[SizeDimension], list[ColorSizes]]:
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


def summarize_sizes_with_sources(
    skus: list[Sku],
) -> tuple[list[SizeDimension], list[ColorSizes], list[list[str]]]:
    """Keep source mapping internal and raw color associations unchanged."""
    summaries, links = _raw_sizes(skus)
    names: dict[tuple[int, str], set[str | None]] = {}
    for sku in skus:
        for spec in sku.specifications:
            names.setdefault((spec.position, spec.field), set()).add(spec.name)
    recognized = {key: _kind(next(iter(values))) for key, values in names.items()
                  if len(values) == 1 and None not in values}
    colors = [key for key, kind in recognized.items() if kind == "color"]
    size_count = sum(kind == "size" for kind in recognized.values())
    groups = None
    if len(colors) == 1 and size_count == 1 and len(summaries) == 1:
        summary = summaries[0]
        size_key = (summary.position, summary.field)
        linked: dict[str, list[str]] = {}
        for sku in skus:
            color = [s for s in sku.specifications if (s.position, s.field) == colors[0]]
            size = [s for s in sku.specifications if (s.position, s.field) == size_key]
            if len(color) != 1 or not isinstance(color[0].value, str) or not color[0].value.strip():
                continue
            # Register colors BEFORE validating sizes: empty groups constrain intersection.
            values = linked.setdefault(color[0].value, [])
            if len(size) == 1 and isinstance(size[0].value, str) and size[0].value.strip():
                values.append(size[0].value)
        groups = list(linked.values())
    sources = []
    final_summaries = []
    for summary in summaries:
        values, originals = final_size_range(summary.values, groups)
        final_summaries.append(SizeDimension(summary.position, summary.field, summary.name, values))
        sources.append(originals)
    return final_summaries, links, sources


def summarize_sizes(skus: list[Sku]) -> tuple[list[SizeDimension], list[ColorSizes]]:
    summaries, links, _ = summarize_sizes_with_sources(skus)
    return summaries, links
