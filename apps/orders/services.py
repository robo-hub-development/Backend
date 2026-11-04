from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.cart.models import Cart, CartItem
from apps.catalog.models import Product

from .models import ORDER_NUMBER_SEQUENCE, Order, OrderItem


def _fail(message: str):
    # {"detail": ...} surfaces as the top-level `message` of the error envelope.
    return ValidationError({"detail": message})


def _next_order_number() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval(%s)", [ORDER_NUMBER_SEQUENCE])
        return cursor.fetchone()[0]


def _lock_products(ids) -> dict:
    """Row-lock products in a fixed (pk) order so concurrent orders cannot deadlock each other."""
    products = Product.objects.select_for_update().filter(pk__in=ids, is_active=True).order_by("pk")
    return {product.pk: product for product in products}


def _main_image_url(product) -> str:
    image = product.media.filter(media_type="image").first()  # default ordering: lowest position first
    return image.file.url if image else ""


def place_order(user, cart_id, cart_item_ids, address: dict, payment_method: str) -> Order:
    """Create an order from the chosen live lines of the caller's cart, reserving stock (COD only for now).

    The client names the cart lines; quantities and prices always come from the cart and the catalogue.
    """
    if payment_method != Order.PaymentMethod.COD:
        raise ValidationError({"paymentMethod": "Online payment is not available yet."})
    # A client-supplied cart id is never trusted: it must be the caller's own cart.
    cart = Cart.objects.filter(pk=cart_id, user=user).first()
    if cart is None:
        raise ValidationError({"cartId": "Unknown cart."})

    with transaction.atomic():
        # Locking the cart serialises double-submits: the second request finds the lines already ordered.
        Cart.objects.select_for_update().get(pk=cart.pk)
        cart_lines = list(
            CartItem.objects.select_for_update()
            .filter(cart=cart, pk__in=cart_item_ids, ordered_at__isnull=True)
            .select_related("product")
            .order_by("product_id")
        )
        # Foreign, unknown and already-ordered ids all look the same: not a live line of this cart.
        if len(cart_lines) != len(set(cart_item_ids)):
            raise ValidationError(
                {"cartItems": "Some cart items are not in your cart or were already ordered."}
            )
        # PRD 1.4: cash on delivery is for one piece only.
        if not (len(cart_lines) == 1 and cart_lines[0].quantity == 1):
            raise _fail("Cash on delivery is available only for a single item.")

        products = _lock_products([line.product_id for line in cart_lines])
        total = Decimal(0)
        purchases = []
        for line in cart_lines:
            product = products.get(line.product_id)
            if product is None:
                raise _fail("A product in your cart is no longer available.")
            if product.stock_quantity < line.quantity:
                raise _fail(f"{product.name} is out of stock.")
            product.stock_quantity -= line.quantity  # safe: the row is locked until commit
            product.save(update_fields=["stock_quantity", "updated_at"])
            total += product.price * line.quantity
            purchases.append((product, line))

        order = Order.objects.create(
            user=user,
            cart=cart,
            number=_next_order_number(),
            payment_method=payment_method,
            total=total,
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
                product=product,
                cart_item=line,
                product_name=product.name,
                product_slug=product.slug,
                image=_main_image_url(product),
                unit_price=product.price,
                quantity=line.quantity,
            )
            for product, line in purchases
        )
        CartItem.objects.filter(pk__in=[line.pk for line in cart_lines]).update(ordered_at=timezone.now())
    return order


def cancel_order(user, order_id) -> Order:
    """Cancel the user's own order if the status map and the cancellation window allow it; restore stock."""
    with transaction.atomic():
        # Lock the order so two concurrent cancels cannot both restore stock. Foreign ids behave as unknown.
        order = Order.objects.select_for_update().filter(user=user, pk=order_id).first()
        if order is None:
            raise NotFound
        if not order.can_cancel:
            raise _fail("This order can no longer be cancelled.")

        items = list(order.items.all())
        products = {
            product.pk: product
            for product in Product.objects.select_for_update()
            .filter(pk__in=[i.product_id for i in items if i.product_id])
            .order_by("pk")
        }
        for item in items:
            product = products.get(item.product_id)
            if product is not None:
                product.stock_quantity += item.quantity
                product.save(update_fields=["stock_quantity", "updated_at"])

        order.status = Order.Status.CANCELLED
        order.cancelled_at = timezone.now()
        order.modified_by = user
        order.save(update_fields=["status", "cancelled_at", "modified_by", "updated_at"])
    return order
