from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel

MAX_QUANTITY_PER_PRODUCT = 10  # PRD 1.4: "upto 10 Qty"; also enforced by the storefront


class Cart(TimeStampedModel):
    """A customer's server-side cart: one per user, created on first use. Orders keep a link back to it."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")

    def __str__(self):
        return f"Cart of {self.user.email}"


class CartItem(TimeStampedModel):
    """One product line in a cart. A line becomes "ordered" (ordered_at set) when an order is placed from it;
    ordered lines stay as history and are no longer part of the live cart."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    quantity = models.PositiveSmallIntegerField()
    ordered_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            # Uniqueness applies to the live cart only, so a product can be added again after it was ordered.
            models.UniqueConstraint(
                fields=["cart", "product"],
                condition=Q(ordered_at__isnull=True),
                name="cartitem_unique_live_product_per_cart",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1) & Q(quantity__lte=MAX_QUANTITY_PER_PRODUCT),
                name="cartitem_quantity_1_to_10",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"
