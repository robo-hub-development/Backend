import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product

from .models import ORDER_NUMBER_SEQUENCE, Order, OrderItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Purchase:
    """One order line to create: what was bought, at which price, and the cart line it came from."""

    product: Product
    quantity: int
    unit_price: Decimal
    cart_item: CartItem | None = None


def _fail(message: str):
    # {"detail": ...} surfaces as the top-level `message` of the error envelope.
    return ValidationError({"detail": message})


def _next_order_number() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval(%s)", [ORDER_NUMBER_SEQUENCE])
        return cursor.fetchone()[0]


def _main_image_url(product) -> str:
    image = product.media.filter(media_type="image").first()  # default ordering: lowest position first
    return image.file.url if image else ""


def lock_cart_lines(user, cart_id, cart_item_ids) -> tuple[Cart, list[CartItem]]:
    """Validate and row-lock the chosen live lines of the caller's cart. Call inside transaction.atomic().

    Foreign, unknown, already-ordered and already-being-paid ids are all rejected, so a line can never be
    fulfilled twice (e.g. a COD order for lines that an online payment is holding).
    """
    # A client-supplied cart id is never trusted: it must be the caller's own cart.
    cart = Cart.objects.select_for_update().filter(pk=cart_id, user=user).first()
    if cart is None:
        raise ValidationError({"cartId": "Unknown cart."})
    lines = list(
        CartItem.objects.select_for_update()
        .filter(cart=cart, pk__in=cart_item_ids, ordered_at__isnull=True)
        .select_related("product")
        .order_by("product_id")
    )
    # Foreign, unknown and already-ordered ids all look the same: not a live line of this cart.
    if len(lines) != len(set(cart_item_ids)):
        raise ValidationError({"cartItems": "Some cart items are not in your cart or were already ordered."})
    # `payment_items` is the reverse relation of payments.PaymentItem.cart_item.
    if CartItem.objects.filter(
        pk__in=[line.pk for line in lines], payment_items__attempt__status="pending"
    ).exists():
        raise ValidationError({"cartItems": "Some cart items are already waiting for a payment."})
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
            raise _fail("A product in your cart is no longer available.")
        if product.stock_quantity < quantity:
            raise _fail(f"{product.name} is out of stock.")
    for product_id, quantity in pairs:
        product = products[product_id]
        product.stock_quantity -= quantity  # safe: the row is locked until commit
        product.save(update_fields=["stock_quantity", "updated_at"])
    return products


def return_stock(pairs: list[tuple]) -> None:
    """Give back stock for [(product_id, quantity)] (cancelled order, failed or expired payment)."""
    ids = [pid for pid, _ in pairs if pid]
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    for product_id, quantity in pairs:
        product = products.get(product_id)
        if product is not None:
            product.stock_quantity += quantity
            product.save(update_fields=["stock_quantity", "updated_at"])


def create_order(
    *, user, cart, purchases: list[Purchase], address: dict, payment_method, payment_status, status
) -> Order:
    """Persist an order with its lines, mapping each line to its cart line and marking those lines ordered.

    Stock is NOT touched here: the caller has already taken it (COD: at checkout; online: when the payment
    was started). Call inside transaction.atomic().
    """
    order = Order.objects.create(
        user=user,
        cart=cart,
        number=_next_order_number(),
        status=status,
        payment_method=payment_method,
        payment_status=payment_status,
        total=sum((p.unit_price * p.quantity for p in purchases), Decimal(0)),
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
    CartItem.objects.filter(pk__in=[p.cart_item.pk for p in purchases if p.cart_item]).update(
        ordered_at=timezone.now()
    )
    return order


def place_order(user, cart_id, cart_item_ids, address: dict) -> Order:
    """Create a cash-on-delivery order from the chosen live lines of the caller's cart, taking stock now.

    The client names the cart lines; quantities and prices always come from the cart and the catalogue.
    Online orders go through apps.payments instead (the order exists only after payment succeeds).
    """
    with transaction.atomic():
        cart, lines = lock_cart_lines(user, cart_id, cart_item_ids)
        # PRD 1.4: cash on delivery is for one piece only.
        if not (len(lines) == 1 and lines[0].quantity == 1):
            raise _fail("Cash on delivery is available only for a single item.")
        products = take_stock([(line.product_id, line.quantity) for line in lines])
        purchases = [
            Purchase(products[line.product_id], line.quantity, products[line.product_id].price, line)
            for line in lines
        ]
        return create_order(
            user=user,
            cart=cart,
            purchases=purchases,
            address=address,
            payment_method=Order.PaymentMethod.COD,
            payment_status=Order.PaymentStatus.PENDING,
            status=Order.Status.PENDING,
        )


def cancel_order(user, order_id) -> Order:
    """Cancel the user's own order if the status map and the cancellation window allow it; restore stock.

    A paid online order is refunded after the cancellation is committed (see payments.services).
    """
    with transaction.atomic():
        # Lock the order so two concurrent cancels cannot both restore stock. Foreign ids behave as unknown.
        order = Order.objects.select_for_update().filter(user=user, pk=order_id).first()
        if order is None:
            raise NotFound
        if not order.can_cancel:
            raise _fail("This order can no longer be cancelled.")

        return_stock([(item.product_id, item.quantity) for item in order.items.all()])
        order.status = Order.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.modified_by = user
        order.save(update_fields=["status", "cancelled_at", "modified_by", "updated_at"])

    if (
        order.payment_method == Order.PaymentMethod.ONLINE
        and order.payment_status == Order.PaymentStatus.PAID
    ):
        from apps.payments import services as payment_services  # lazy: payments depends on this module

        payment_services.refund_cancelled_order(order)
        order.refresh_from_db()
    return order
