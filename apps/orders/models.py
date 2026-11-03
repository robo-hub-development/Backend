from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.common.models import AuditModel, TimeStampedModel

ORDER_NUMBER_PREFIX = "SRH-"
ORDER_NUMBER_SEQUENCE = "orders_order_number_seq"  # Postgres sequence created in 0001; starts at 1001


class Order(AuditModel):
    """A placed order. Prices and the address are snapshots: later catalogue/profile edits never alter it."""

    class Status(models.TextChoices):
        PENDING = "pending"
        CONFIRMED = "confirmed"
        SHIPPED = "shipped"
        DELIVERED = "delivered"
        CANCELLED = "cancelled"

    class PaymentMethod(models.TextChoices):
        COD = "cod"
        ONLINE = "online"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        FAILED = "failed"
        REFUNDED = "refunded"

    # Allowed status moves; every writer (customer cancel, staff actions) must go through this map.
    TRANSITIONS = {
        Status.PENDING: {Status.CONFIRMED, Status.CANCELLED},
        Status.CONFIRMED: {Status.SHIPPED, Status.CANCELLED},
        Status.SHIPPED: {Status.DELIVERED},
        Status.DELIVERED: set(),
        Status.CANCELLED: set(),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    number = models.PositiveIntegerField(unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    payment_method = models.CharField(max_length=6, choices=PaymentMethod)
    payment_status = models.CharField(max_length=8, choices=PaymentStatus, default=PaymentStatus.PENDING)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Delivery address snapshot.
    ship_full_name = models.CharField(max_length=120)
    ship_mobile = models.CharField(max_length=16)
    ship_line1 = models.CharField(max_length=200)
    ship_line2 = models.CharField(max_length=200, blank=True)
    ship_city = models.CharField(max_length=100)
    ship_state = models.CharField(max_length=100)
    ship_pincode = models.CharField(max_length=6)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "-created_at"], name="order_user_recent_idx")]
        constraints = [models.CheckConstraint(condition=Q(total__gte=0), name="order_total_non_negative")]

    def __str__(self):
        return self.order_number

    @property
    def order_number(self) -> str:
        return f"{ORDER_NUMBER_PREFIX}{self.number}"

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.TRANSITIONS[self.status]

    @property
    def can_cancel(self) -> bool:
        """Decided here, never by the client: a legal move to cancelled, inside the cancellation window."""
        window = timedelta(hours=settings.ORDER_CANCEL_WINDOW_HOURS)
        return self.can_transition_to(self.Status.CANCELLED) and timezone.now() <= self.created_at + window


class OrderItem(TimeStampedModel):
    """Line of an order with the name/price/image as they were at purchase time."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    # SET_NULL: deleting a product from the catalogue must not erase purchase history.
    product = models.ForeignKey("catalog.Product", null=True, on_delete=models.SET_NULL, related_name="+")
    product_name = models.CharField(max_length=200)
    product_slug = models.SlugField(max_length=220)
    image = models.CharField(max_length=500, blank=True, help_text="Media URL at purchase time.")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="orderitem_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="orderitem_price_non_negative"),
        ]

    def __str__(self):
        return f"{self.product_name} x{self.quantity}"
