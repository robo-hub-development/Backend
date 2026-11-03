from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel

MAX_QUANTITY_PER_PRODUCT = 10  # PRD 1.4: "upto 10 Qty"; also enforced by the storefront


class CartItem(TimeStampedModel):
    """One product line in a customer's server-side cart. The set of a user's rows *is* their cart."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart_items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    quantity = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "product"], name="cartitem_unique_product_per_user"),
            models.CheckConstraint(
                condition=Q(quantity__gte=1) & Q(quantity__lte=MAX_QUANTITY_PER_PRODUCT),
                name="cartitem_quantity_1_to_10",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.product_id}x{self.quantity}"
