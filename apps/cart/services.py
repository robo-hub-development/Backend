from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product

from .models import Cart, CartItem


def live_items(user):
    """The user's current cart lines: not yet ordered, and only for active products."""
    return CartItem.objects.filter(cart__user=user, ordered_at__isnull=True, product__is_active=True)


def cart_id_for(user):
    return Cart.objects.filter(user=user).values_list("pk", flat=True).first()


def get_cart(user):
    return list(live_items(user).select_related("product").prefetch_related("product__media"))


def replace_cart(user, lines: list[dict]):
    """Atomically replace the live cart with `lines` ([{product_id, quantity}], already shape-validated).

    Prices are never accepted from the client; they are read from the product when the cart is rendered.
    Ordered lines are history and are left untouched.
    """
    ids = [line["product_id"] for line in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError({"items": "Each product may appear only once."})
    active = set(Product.objects.filter(pk__in=ids, is_active=True).values_list("pk", flat=True))
    if missing := [str(pk) for pk in ids if pk not in active]:
        raise ValidationError({"items": f"Unknown or unavailable product: {missing[0]}"})

    with transaction.atomic():
        cart, _ = Cart.objects.get_or_create(user=user)
        Cart.objects.select_for_update().get(pk=cart.pk)  # serialise concurrent PUTs for the same user
        CartItem.objects.filter(cart=cart, ordered_at__isnull=True).delete()
        CartItem.objects.bulk_create(
            CartItem(cart=cart, product_id=line["product_id"], quantity=line["quantity"]) for line in lines
        )
    return get_cart(user)


def remove_item(user, item_id):
    """Delete one live line of the caller's own cart. Ordered lines are history and cannot be removed;
    foreign, unknown and ordered ids all behave as "not found" so ids of other carts never leak."""
    deleted, _ = CartItem.objects.filter(cart__user=user, pk=item_id, ordered_at__isnull=True).delete()
    if not deleted:
        raise NotFound
    return get_cart(user)
