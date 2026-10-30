from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

CONVENIENCE_FEE = Decimal("30")
GST_RATE = Decimal("0.18")


def q(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calc_bill(prices: Iterable) -> dict:
    prices = [Decimal(p) for p in prices]
    ticket_total = q(sum(prices, Decimal("0")))
    fee = q(CONVENIENCE_FEE * len(prices))
    gst = q((ticket_total + fee) * GST_RATE)
    return {"ticket_total": ticket_total, "convenience_fee": fee, "gst_amount": gst,
            "grand_total": q(ticket_total + fee + gst)}


def dynamic_price(base, booked: int, total: int) -> Decimal:
    """+10% when a show is more than 80% booked (bonus feature)."""
    base = Decimal(base)
    if total and booked / total > 0.8:
        return q(base * Decimal("1.10"))
    return q(base)


def refund_percent(seconds_before_show: float) -> Optional[Decimal]:
    """>24h: 100%, 2h..24h: 50%, <2h: not allowed (None)."""
    hours = seconds_before_show / 3600
    if hours > 24:
        return Decimal("100")
    if hours >= 2:
        return Decimal("50")
    return None
