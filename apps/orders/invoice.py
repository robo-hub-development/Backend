"""Printable invoice for an order, shown from the admin so it can go in the parcel.

Nothing here writes to the database. The invoice number is derived from the order number (INV-SRH-1017), so
reprinting always gives the same document. Amounts are printed as charged; no tax is split out.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from .models import Order

_ONES = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen "
    "Seventeen Eighteen Nineteen"
).split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_hundred(n: int) -> str:
    return _ONES[n] if n < 20 else _TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = [f"{_ONES[hundreds]} Hundred"] if hundreds else []
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def rupees_in_words(amount: Decimal) -> str:
    """Indian numbering (lakh, crore): Decimal("1234.50") -> "Rupees One Thousand Two Hundred Thirty Four and
    Fifty Paise Only"."""
    paise_total = int((Decimal(amount) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    rupees, paise = divmod(paise_total, 100)
    parts = []
    for name, size in (("Crore", 10_000_000), ("Lakh", 100_000), ("Thousand", 1_000)):
        count, rupees = divmod(rupees, size)
        if count:
            parts.append(f"{_below_hundred(count) if count < 100 else _below_thousand(count)} {name}")
    if rupees:
        parts.append(_below_thousand(rupees))
    words = " ".join(parts) or "Zero"
    text = f"Rupees {words}"
    if paise:
        text += f" and {_below_hundred(paise)} Paise"
    return f"{text} Only"


def invoice_context(order: Order) -> dict:
    """Everything the invoice template prints. `order` should have items and charges prefetched."""
    items = [
        {
            "name": item.product_name,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "amount": item.unit_price * item.quantity,
        }
        for item in order.items.all()
    ]
    cash_due = order.amount_due if order.payment_method == Order.PaymentMethod.COD else Decimal(0)
    return {
        "order": order,
        "seller": settings.SELLER,
        "invoice_number": f"INV-{order.order_number}",
        "items": items,
        "charges": list(order.charges.all()),
        "cash_due": cash_due,  # what the courier collects (COD); zero for prepaid orders
        "in_words": rupees_in_words(order.total),
        "ship": {
            "name": order.ship_full_name,
            "mobile": order.ship_mobile,
            "lines": [line for line in (order.ship_line1, order.ship_line2) if line],
            "city_line": f"{order.ship_city}, {order.ship_state} - {order.ship_pincode}",
        },
        "not_for_dispatch": order.status in Order.DEAD_STATUSES,
    }
