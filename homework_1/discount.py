# ===========================================================================
# discount.py  --  tiered discount formula engine for Tool 2
# ===========================================================================

from decimal import Decimal, ROUND_HALF_UP


# Flat tiers: the selected rate applies to the entire order.
# Calibrated against the product catalog's stock_qty range (6-140 units,
# median 24) so both tiers are actually reachable: ~90% of SKUs can hit the
# 5% tier and ~37% can hit the 12% tier at their full current stock.
TIERS = ((0, 0.00), (10, 0.05), (30, 0.12))


def discount_rate(qty: int) -> float:
    if qty < 0:
        raise ValueError("qty must be non-negative")
    rate = 0.0
    for minimum, candidate in TIERS:
        if qty >= minimum:
            rate = candidate
        else:
            break
    return rate


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_discount(qty: int, unit_price: float) -> dict:
    if unit_price < 0:
        raise ValueError("unit_price must be non-negative")
    rate = discount_rate(qty)
    subtotal = Decimal(qty) * Decimal(str(unit_price))
    total = subtotal * (Decimal("1") - Decimal(str(rate)))
    return {
        "qty": qty,
        "unit_price": _money(Decimal(str(unit_price))),
        "rate": rate,
        "subtotal": _money(subtotal),
        "discount_amount": _money(subtotal - total),
        "total": _money(total),
    }
