"""Final size ranges and aligned original values; never mutate SKU options."""
import re


def size_rank(value: str) -> int | None:
    """S=0, M=1, L=2, XL=3; XS=-1, XXS=-2, XXL=2XL=4."""
    normalized = value.strip().upper()
    if normalized in {"S", "M", "L"}:
        return {"S": 0, "M": 1, "L": 2}[normalized]
    match = re.fullmatch(r"(X+)(S|L)", normalized)
    if match:
        count = len(match[1])
        return -count if match[2] == "S" else count + 2
    match = re.fullmatch(r"([2-9]|[1-9][0-9]+)X(S|L)", normalized)
    if match:
        count = int(match[1])
        return -count if match[2] == "S" else count + 2
    return None


def _label(rank: int) -> str:
    if rank < 0:
        return "XS" if rank == -1 else f"{-rank}XS"
    if rank <= 3:
        return ("S", "M", "L", "XL")[rank]
    return f"{rank - 2}XL"


def _identity(value: str) -> int | str:
    rank = size_rank(value)
    return value if rank is None else rank


def final_size_range(
    raw_values: list[str], color_values: list[list[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Intersect before shifting; each displayed value keeps its first raw source."""
    sources = {}
    for value in raw_values:
        sources.setdefault(_identity(value), value)
    retained = set(sources)
    if color_values is not None:
        for values in color_values:
            retained.intersection_update(_identity(value) for value in values)
    ranks = sorted(key for key in retained if isinstance(key, int))
    other = [key for key in sources if isinstance(key, str) and key in retained]
    shift = max(ranks[0], 0) if ranks else 0
    values = [_label(rank - shift) for rank in ranks] + other
    originals = [sources[key] for key in [*ranks, *other]]
    return values, originals
