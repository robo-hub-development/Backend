"""Low-level order operations shared by checkout, payments and (later) staff actions.

Everything here assumes the caller holds the right locks and runs inside transaction.atomic(); the
orchestration across carts, payments and the gateway lives in apps.orders.checkout.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import connection
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product
from apps.notifications import services as notifications

from .models import ORDER_NUMBER_SEQUENCE, Order, OrderCharge, OrderItem, OrderStatusEvent

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """A status change the transition map does not allow (a programming error, not a user error)."""


@dataclass(frozen=True)
class Purchase:
    """One order line to create: what was bought, at which price, and the cart line it came from."""

    product: Product
    quantity: int
    unit_price: Decimal
    cart_item: CartItem | None = None


@dataclass(frozen=True)
class ChargeSpec:
    kind: str
    label: str
    amount: Decimal
    rule: dict = field(default_factory=dict)


def fail(message: str):
    # {"detail": ...} surfaces as the top-level `message` of the error envelope.
    return ValidationError({"detail": message})


def _next_order_number() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval(%s)", [ORDER_NUMBER_SEQUENCE])
        return cursor.fetchone()[0]


def _main_image_url(product) -> str:
    image = product.media.filter(media_type="image").first()  # default ordering: lowest position first
    return image.file.url if image else ""


def lock_cart_lines(user, cart_id, cart_item_ids, *, lock=True) -> tuple[Cart, list[CartItem]]:
    """Validate (and, with lock=True, row-lock) the chosen live lines of the caller's cart.

    Foreign, unknown, already-ordered and already-being-paid ids are all rejected, so a line can never be
    fulfilled twice (e.g. a COD order for lines that an unpaid order is holding). Locking needs
    transaction.atomic(); quotes pass lock=False.
    """
    carts = Cart.objects.select_for_update() if lock else Cart.objects
    # A client-supplied cart id is never trusted: it must be the caller's own cart.
    cart = carts.filter(pk=cart_id, user=user).first()
    if cart is None:
        raise ValidationError({"cartId": "Unknown cart."})
    lines_qs = CartItem.objects.select_for_update() if lock else CartItem.objects
    lines = list(
        lines_qs.filter(cart=cart, pk__in=cart_item_ids, ordered_at__isnull=True)
        .select_related("product")
        .order_by("product_id")
    )
    # Foreign, unknown and already-ordered ids all look the same: not a live line of this cart.
    if len(lines) != len(set(cart_item_ids)):
        raise ValidationError({"cartItems": "Some cart items are not in your cart or were already ordered."})
    held = CartItem.objects.filter(
        pk__in=[line.pk for line in lines], order_items__order__status=Order.Status.AWAITING_PAYMENT
    )
    if held.exists():
        raise ValidationError(
            {"cartItems": "Some cart items belong to an order that is waiting for payment."}
        )
    return cart, lines


def take_stock(pairs: list[tuple]) -> dict:
    """Decrement stock for [(product_id, quantity)] or raise; returns {product_id: Product}.

    Rows are locked in pk order so concurrent callers cannot deadlock. Call inside transaction.atomic().
    """
    products = {
        p.pk: p
        for p in Product.objects.select_for_update()
        .filter(pk__in=[pid for pid, _ in pairs], is_active=True)
        .order_by("pk")
    }
    for product_id, quantity in pairs:
        product = products.get(product_id)
        if product is None:
            raise fail("A product in your cart is no longer available.")
        if product.stock_quantity < quantity:
            raise fail(f"{product.name} is out of stock.")
    for product_id, quantity in pairs:
        product = products[product_id]
        product.stock_quantity -= quantity  # safe: the row is locked until commit
        product.save(update_fields=["stock_quantity", "updated_at"])
    return products


def return_stock(pairs: list[tuple]) -> None:
    """Give back stock for [(product_id, quantity)] (cancelled or expired order)."""
    ids = [pid for pid, _ in pairs if pid]
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    for product_id, quantity in pairs:
        product = products.get(product_id)
        if product is not None:
            product.stock_quantity += quantity
            product.save(update_fields=["stock_quantity", "updated_at"])


def return_order_stock(order: Order) -> None:
    return_stock([(item.product_id, item.quantity) for item in order.items.all()])


def transition(order: Order, to_status: str, *, actor=None, reason: str = "") -> Order:
    """The only way an order's status changes: checks the transition map and writes the audit event.

    The caller must hold a row lock on the order. `actor` is None when the system acts (webhook, expiry job).
    """
    if not order.can_transition_to(to_status):
        raise InvalidTransition(f"{order.order_number}: {order.status} -> {to_status}")
    previous = order.status
    order.status = to_status
    fields = ["status", "updated_at"]
    if to_status == Order.Status.CANCELLED:
        order.cancelled_at = timezone.now()
        fields.append("cancelled_at")
    if actor is not None:
        order.modified_by = actor
        fields.append("modified_by")
    order.save(update_fields=fields)
    OrderStatusEvent.objects.create(
        order=order, from_status=previous, to_status=to_status, actor=actor, reason=reason[:200]
    )
    if to_status == Order.Status.DELIVERED:
        notifications.after_commit(notifications.notify_order_delivered, order.pk)
    if to_status == Order.Status.SHIPPED:
        notifications.after_commit(notifications.notify_order_shipped, order.pk)
        # The invoice goes once as well; staff can resend it from the admin.
        notifications.after_commit(notifications.send_invoice, order.pk)
    return order


def create_order(
    *,
    user,
    cart,
    purchases: list[Purchase],
    charges: list[ChargeSpec],
    address: dict,
    payment_method: str,
    status: str,
    payment_due_by=None,
) -> Order:
    """Persist an order with its lines and charges, mapping each line to its cart line.

    Stock is NOT touched and cart lines are NOT marked ordered here: the caller takes the stock, and calls
    commit_cart_lines once the order no longer waits for payment. Call inside transaction.atomic().
    """
    subtotal = sum((p.unit_price * p.quantity for p in purchases), Decimal(0))
    charges_total = sum((c.amount for c in charges), Decimal(0))
    order = Order.objects.create(
        user=user,
        cart=cart,
        number=_next_order_number(),
        status=status,
        payment_method=payment_method,
        subtotal=subtotal,
        charges_total=charges_total,
        total=subtotal + charges_total,
        payment_due_by=payment_due_by,
        created_by=user,
        modified_by=user,
        ship_full_name=address["full_name"],
        ship_mobile=address["mobile"],
        ship_line1=address["line1"],
        ship_line2=address.get("line2", ""),
        ship_city=address["city"],
        ship_state=address["state"],
        ship_pincode=address["pincode"],
    )
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            product=p.product,
            cart_item=p.cart_item,
            product_name=p.product.name,
            product_slug=p.product.slug,
            image=_main_image_url(p.product),
            unit_price=p.unit_price,
            quantity=p.quantity,
        )
        for p in purchases
    )
    OrderCharge.objects.bulk_create(
        OrderCharge(order=order, kind=c.kind, label=c.label, amount=c.amount, rule=c.rule) for c in charges
    )
    OrderStatusEvent.objects.create(
        order=order, from_status="", to_status=status, actor=user, reason="placed"
    )
    return order


def commit_cart_lines(order: Order) -> None:
    """Mark the cart lines behind this order as ordered (they leave the live cart)."""
    CartItem.objects.filter(
        pk__in=order.items.exclude(cart_item__isnull=True).values_list("cart_item_id", flat=True),
        ordered_at__isnull=True,
    ).update(ordered_at=timezone.now())
