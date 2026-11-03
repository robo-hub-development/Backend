from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.cart.models import CartItem
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


def place_order(user, lines: list[dict], address: dict, payment_method: str) -> Order:
    """Create an order from `lines` ([{product_id, quantity}]), reserving stock. Only COD is available yet."""
    if payment_method != Order.PaymentMethod.COD:
        raise ValidationError({"paymentMethod": "Online payment is not available yet."})

    ids = [line["product_id"] for line in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError({"items": "Each product may appear only once."})
    # PRD 1.4: cash on delivery is for one piece only.
    if not (len(lines) == 1 and lines[0]["quantity"] == 1):
        raise _fail("Cash on delivery is available only for a single item.")

    with transaction.atomic():
        products = _lock_products(ids)
        total = Decimal(0)
        items = []
        for line in lines:
            product = products.get(line["product_id"])
            if product is None:
                raise _fail("A product in your order is no longer available.")
            if product.stock_quantity < line["quantity"]:
                raise _fail(f"{product.name} is out of stock.")
            product.stock_quantity -= line["quantity"]  # safe: the row is locked until commit
            product.save(update_fields=["stock_quantity", "updated_at"])
            total += product.price * line["quantity"]
            items.append((product, line["quantity"]))

        order = Order.objects.create(
            user=user,
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
                product_name=product.name,
                product_slug=product.slug,
                image=_main_image_url(product),
                unit_price=product.price,
                quantity=quantity,
            )
            for product, quantity in items
        )
        CartItem.objects.filter(user=user, product_id__in=ids).delete()
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
