"""Read-only numbers for the admin dashboard. One query per figure group; nothing here writes.

"Revenue" is the value (`Order.total`: items plus charges such as the COD fee) of orders really placed:
pending, confirmed, shipped or delivered. Unpaid (`awaiting_payment`), `expired`, `cancelled` and `rto` orders
are not counted. It is booked revenue, not cash collected: COD cash arrives on delivery. Periods follow
settings.TIME_ZONE.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncYear
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.orders.models import Order, OrderItem
from apps.payments.models import Payment

REVENUE_STATUSES = (
    Order.Status.PENDING,
    Order.Status.CONFIRMED,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
)
LOW_STOCK_AT = 5  # products with this many units or fewer are listed as low on stock
MONTHS_SHOWN, DAYS_SHOWN, YEARS_SHOWN, TOP_PRODUCTS, RECENT_ORDERS = 12, 30, 5, 5, 10
ZERO = Decimal(0)


@dataclass(frozen=True)
class Bar:
    label: str
    revenue: Decimal
    orders: int
    height: int  # percent of the tallest bar; at least 3 for a non-zero value so it stays visible


def _money(value) -> Decimal:
    return value or ZERO


def _bars(rows: dict[date, dict], periods: list[date], label) -> list[Bar]:
    tallest = max((rows.get(p, {}).get("revenue") or ZERO for p in periods), default=ZERO)
    bars = []
    for period in periods:
        revenue = _money(rows.get(period, {}).get("revenue"))
        height = 0 if revenue == 0 or tallest == 0 else max(3, round(revenue / tallest * 100))
        bars.append(Bar(label(period), revenue, rows.get(period, {}).get("orders", 0), height))
    return bars


def _grouped(trunc, since: datetime) -> dict[date, dict]:
    rows = (
        Order.objects.filter(status__in=REVENUE_STATUSES, created_at__gte=since)
        .annotate(period=trunc("created_at"))
        .values("period")
        .annotate(revenue=Sum("total"), orders=Count("id"))
    )
    return {_as_date(row["period"]): row for row in rows}


def _as_date(value) -> date:
    return timezone.localtime(value).date() if isinstance(value, datetime) else value


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month(day: date) -> date:
    return _month_start(_month_start(day) - timedelta(days=1))


def _start_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, datetime.min.time()))


def daily(today: date) -> list[Bar]:
    days = [today - timedelta(days=n) for n in range(DAYS_SHOWN - 1, -1, -1)]
    rows = _grouped(TruncDate, _start_of(days[0]))
    return _bars(rows, days, lambda d: d.strftime("%d %b"))


def monthly(today: date) -> list[Bar]:
    months = [_month_start(today)]
    while len(months) < MONTHS_SHOWN:
        months.insert(0, _previous_month(months[0]))
    rows = _grouped(TruncMonth, _start_of(months[0]))
    return _bars(rows, months, lambda d: d.strftime("%b %y"))


def yearly(today: date) -> list[Bar]:
    years = [date(year, 1, 1) for year in range(today.year - YEARS_SHOWN + 1, today.year + 1)]
    rows = _grouped(TruncYear, _start_of(years[0]))
    return _bars(rows, years, lambda d: str(d.year))


def _totals(today: date) -> dict:
    month, last_month, year = _month_start(today), _previous_month(today), date(today.year, 1, 1)
    zero = Decimal(0)

    def revenue(**window):
        return Coalesce(Sum("total", filter=Q(**window)), zero, output_field=DecimalField())

    t_today, t_month, t_last, t_year = (_start_of(d) for d in (today, month, last_month, year))
    return Order.objects.filter(status__in=REVENUE_STATUSES).aggregate(
        revenue_today=revenue(created_at__gte=t_today),
        revenue_month=revenue(created_at__gte=t_month),
        revenue_last_month=revenue(created_at__gte=t_last, created_at__lt=t_month),
        revenue_year=revenue(created_at__gte=t_year),
        revenue_all=revenue(),
        orders_today=Count("id", filter=Q(created_at__gte=t_today)),
        orders_month=Count("id", filter=Q(created_at__gte=t_month)),
        orders_year=Count("id", filter=Q(created_at__gte=t_year)),
        orders_all=Count("id"),
    )


def _change_percent(current: Decimal, previous: Decimal) -> int | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100)


STATUS_LABELS = {Order.Status.RTO: "Returned to origin"}  # the auto-generated "Rto" reads badly


def _status_breakdown() -> list[dict]:
    counts = dict(Order.objects.values_list("status").annotate(n=Count("id")))
    biggest = max(counts.values(), default=0)
    return [
        {
            "status": value,
            "label": STATUS_LABELS.get(value, label),
            "count": counts.get(value, 0),
            "width": round(counts.get(value, 0) / biggest * 100) if biggest else 0,
        }
        for value, label in Order.Status.choices
    ]


def _top_products(since: datetime) -> list[dict]:
    line_value = ExpressionWrapper(F("unit_price") * F("quantity"), output_field=DecimalField())
    return list(
        OrderItem.objects.filter(order__status__in=REVENUE_STATUSES, order__created_at__gte=since)
        .values("product_name")
        .annotate(units=Sum("quantity"), revenue=Sum(line_value))
        .order_by("-revenue")[:TOP_PRODUCTS]
    )


def build_dashboard() -> dict:
    today = timezone.localdate()
    totals = _totals(today)
    month_users = User.objects.filter(is_staff=False, created_at__gte=_start_of(_month_start(today)))
    low_stock = Product.objects.filter(is_active=True, stock_quantity__lte=LOW_STOCK_AT).order_by(
        "stock_quantity", "name"
    )
    return {
        "totals": totals,
        "month_change": _change_percent(totals["revenue_month"], totals["revenue_last_month"]),
        "average_order_value": totals["revenue_all"] / totals["orders_all"] if totals["orders_all"] else ZERO,
        "daily": daily(today),
        "monthly": monthly(today),
        "yearly": yearly(today),
        "statuses": _status_breakdown(),
        "top_products": _top_products(_start_of(today - timedelta(days=DAYS_SHOWN - 1))),
        "attention": {
            "to_accept": Order.objects.filter(status=Order.Status.PENDING).count(),
            "awaiting_payment": Order.objects.filter(status=Order.Status.AWAITING_PAYMENT).count(),
            "needs_refund": Payment.objects.filter(status=Payment.Status.NEEDS_REFUND).count(),
            "refunds_in_progress": Payment.objects.filter(status=Payment.Status.REFUND_IN_PROGRESS).count(),
            "low_stock": low_stock.count(),
        },
        "low_stock_products": list(low_stock[:8]),
        "customers": {
            "total": User.objects.filter(is_staff=False).count(),
            "new_this_month": month_users.count(),
        },
        "recent_orders": list(Order.objects.select_related("user").order_by("-created_at")[:RECENT_ORDERS]),
        "low_stock_at": LOW_STOCK_AT,
        "days_shown": DAYS_SHOWN,
    }
