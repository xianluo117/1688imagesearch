"""SKU HTTP-only amount presentation; never used for quote comparison."""
import re
from decimal import Decimal, ROUND_CEILING, localcontext


def present_price(amount: str | None) -> str | None:
    """Ceil a positive decimal string, preserving null/legacy invalid values.

    Validation and quote selection belong to the caller. In particular, v2
    must finish its availability check before calling this presentation helper.
    """
    if not isinstance(amount, str) or re.fullmatch(
        r"[0-9]{1,40}(?:\.[0-9]{1,24})?", amount,
    ) is None:
        return amount
    value = Decimal(amount)
    if value <= 0:
        return amount
    with localcontext() as context:
        # Include all input digits and a possible carry into a 41st integer digit.
        context.prec = max(context.prec, len(value.as_tuple().digits) + 1)
        return format(value.to_integral_value(rounding=ROUND_CEILING), "f")
