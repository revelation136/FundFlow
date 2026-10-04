"""Money helpers.

All amounts are stored and exchanged as integer centavos so the ledger never
suffers from floating-point drift.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def to_cents(value) -> int:
    """Parse a peso amount ("1,234.56", 1234.56, Decimal) into integer centavos."""
    if isinstance(value, bool):
        raise ValueError("invalid amount")
    if isinstance(value, int):
        return value * 100
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt(cents: int, symbol: str = "₱") -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}{symbol}{whole:,}.{frac:02d}"


def split_pro_rata(total: int, weights: dict) -> dict:
    """Split an integer ``total`` across keys in proportion to positive weights.

    Uses the largest-remainder method so the parts always sum exactly to
    ``total``. Keys with non-positive weight receive nothing.
    """
    if total < 0:
        return {k: -v for k, v in split_pro_rata(-total, weights).items()}
    positive = {k: w for k, w in weights.items() if w > 0}
    if total == 0 or not positive:
        return {}
    weight_sum = sum(positive.values())
    parts, remainders = {}, []
    allotted = 0
    for k, w in positive.items():
        q, r = divmod(total * w, weight_sum)
        parts[k] = q
        allotted += q
        remainders.append((r, k))
    # Hand out the leftover centavos to the largest remainders (stable order).
    remainders.sort(key=lambda item: -item[0])
    for _, k in remainders[: total - allotted]:
        parts[k] += 1
    return {k: v for k, v in parts.items() if v}
