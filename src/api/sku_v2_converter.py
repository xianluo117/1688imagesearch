"""Pure final-result conversion: no I/O, querying, price inference or mutation."""
import re
from decimal import Decimal

from product_sku.models import Sku, SkuResult, Specification
from product_sku.size_summary import COLOR_NAMES, SIZE_NAMES, summarize_sizes
from product_sku.specification_images import safe_image_url

from .sku_price_presentation import present_price
from .sku_v2_schemas import (
    ColorSizesV2, DataV2, DimensionV2, MetaV2, OptionV2, PriceV2,
    ProductSkuResponseV2, ProductV2, SellerV2, SizeDimensionV2, SizeSummaryV2, SkuV2,
)


def _identity(spec: Specification) -> tuple:
    return spec.position, spec.field, spec.name, spec.value


def _price(sku: Sku) -> PriceV2:
    # Availability depends only on a positive decimal amount. Metadata is kept
    # for response compatibility, never used to reject an original/discount quote.
    available = (
        isinstance(sku.price, str)
        and re.fullmatch(r"[0-9]{1,40}(?:\.[0-9]{1,24})?", sku.price) is not None
        and Decimal(sku.price) > 0
    )
    return PriceV2(
        amount=present_price(sku.price) if available else None,
        currency=sku.currency if available else None,
        status="available" if available else "unavailable",
        source=sku.price_source if available else None,
        basis=sku.price_basis if available else None,
    )


def convert_result(result: SkuResult) -> ProductSkuResponseV2:
    names: dict[tuple, set[str | None]] = {}
    for sku in result.skus:
        for spec in sku.specifications:
            names.setdefault((spec.position, spec.field), set()).add(spec.name)

    def dimension_key(spec: Specification) -> tuple:
        candidates = names[(spec.position, spec.field)]
        # A missing name plus a single known name is one unknown dimension, not
        # two artificial dimensions. Explicit conflicting names must not merge.
        name = spec.name if len(candidates - {None}) > 1 else (
            next(iter(candidates)) if len(candidates) == 1 else None
        )
        return spec.position, spec.field, name

    image_candidates: dict[tuple, set[str | None]] = {}
    for image in result.specification_images:
        key = (image.position, image.field, image.name, image.value)
        image_candidates.setdefault(key, set()).add(safe_image_url(image.image_url))

    dimensions: dict[tuple, DimensionV2] = {}
    options: dict[tuple, OptionV2] = {}
    identities: dict[tuple, set[tuple]] = {}
    references: dict[tuple, str] = {}
    for sku in result.skus:
        for spec in sku.specifications:
            key = dimension_key(spec)
            if key not in dimensions:
                candidates = names[(spec.position, spec.field)]
                normalized = spec.name.strip().casefold() if spec.name else ""
                role = "unknown"
                if len(candidates) == 1 and None not in candidates:
                    if normalized in COLOR_NAMES:
                        role = "color"
                    elif normalized in SIZE_NAMES:
                        role = "size"
                dimensions[key] = DimensionV2(
                    dimension_id=f"d{len(dimensions) + 1}", position=spec.position,
                    name=key[2], role=role, options=[],
                )
            dimension = dimensions[key]
            option_key = (key, spec.value)
            if option_key not in options:
                option = OptionV2(
                    option_id=f"{dimension.dimension_id}_o{len(dimension.options) + 1}",
                    value=spec.value, image_url=None,
                )
                dimension.options.append(option)
                options[option_key] = option
            identities.setdefault(option_key, set()).add(_identity(spec))
            references[_identity(spec)] = options[option_key].option_id

    for key, option in options.items():
        # Every original identity must agree; absent/unverified images are not
        # replaced by another name's image or by the product's main image.
        candidates = set()
        for identity in identities[key]:
            candidates.update(image_candidates.get(identity, {None}))
        option.image_url = next(iter(candidates)) if len(candidates) == 1 else None

    sizes, color_sizes = summarize_sizes(result.skus)
    size_dimensions = []
    for size in sizes:
        key = (size.position, size.field, size.name)
        dimension = dimensions[key]
        size_dimensions.append(SizeDimensionV2(
            dimension_id=dimension.dimension_id, name=size.name, values=size.values,
            option_ids=[options[(key, value)].option_id for value in size.values],
        ))
    by_color = []
    for group in color_sizes:
        key = (group.size_position, group.size_field, group.size_name)
        by_color.append(ColorSizesV2(
            color_option_id=references[_identity(group.color)], color=group.color.value,
            size_dimension_id=dimensions[key].dimension_id, sizes=group.values,
            size_option_ids=[options[(key, value)].option_id for value in group.values],
        ))
    return ProductSkuResponseV2(
        status=result.status,
        data=DataV2(
            product=ProductV2(id=result.product_id, url=result.canonical_url, main_image=result.main_image),
            seller=SellerV2(user_id=result.seller_user_id, member_id=result.seller_member_id),
            specifications=list(dimensions.values()),
            size_summary=SizeSummaryV2(dimensions=size_dimensions, by_color=by_color),
            skus=[SkuV2(
                sku_id=sku.sku_id, spec_id=sku.spec_id,
                option_ids=[references[_identity(spec)] for spec in sku.specifications],
                price=_price(sku),
            ) for sku in result.skus],
        ),
        meta=MetaV2(source=result.source, sku_count=len(result.skus),
                    completeness=result.completeness, reason=result.reason, warnings=list(result.warnings)),
    )
