from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product

from .models import CartItem


def get_cart(user):
    """Cart lines for active products only (a product switched off after being added just disappears)."""
    return list(
        CartItem.objects.filter(user=user, product__is_active=True)
        .select_related("product")
        .prefetch_related("product__media")
    )


def replace_cart(user, lines: list[dict]):
    """Atomically replace the whole cart with `lines` ([{product_id, quantity}], already shape-validated).

    Prices are never accepted from the client; they are read from the product when the cart is rendered.
    """
    ids = [line["product_id"] for line in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError({"items": "Each product may appear only once."})
    active = set(Product.objects.filter(pk__in=ids, is_active=True).values_list("pk", flat=True))
    if missing := [str(pk) for pk in ids if pk not in active]:
        raise ValidationError({"items": f"Unknown or unavailable product: {missing[0]}"})

    with transaction.atomic():
        CartItem.objects.filter(user=user).delete()
        CartItem.objects.bulk_create(
            CartItem(user=user, product_id=line["product_id"], quantity=line["quantity"]) for line in lines
        )
    return get_cart(user)
