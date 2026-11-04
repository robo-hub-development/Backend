import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import AuditModel, TimeStampedModel


def new_reference() -> str:
    """Public, unguessable payment reference (sent to the gateway and used in the return URL)."""
    return uuid.uuid4().hex


class PaymentAttempt(AuditModel):
    """One online payment for a set of cart lines. The order is created only when the payment succeeds.

    While PENDING the attempt holds stock for its items (taken when it started, given back if it fails).
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"  # money received and the order exists
        FAILED = "failed"  # cancelled / expired / could not start; reserved stock was released
        NEEDS_REFUND = "needs_refund"  # money received but no order could be made: staff must refund

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_attempts"
    )
    cart = models.ForeignKey(
        "cart.Cart", null=True, on_delete=models.SET_NULL, related_name="payment_attempts"
    )
    reference = models.CharField(max_length=32, unique=True, default=new_reference, editable=False)
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    # Delivery address snapshot (same keys the order uses), applied when the order is created.
    address = models.JSONField()
    expires_at = models.DateTimeField()

    gateway_link_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    short_url = models.URLField(max_length=500, blank=True)
    gateway_payment_id = models.CharField(max_length=64, blank=True)
    failure_reason = models.CharField(max_length=40, blank=True)

    order = models.OneToOneField(
        "orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_attempt"
    )
    refund_id = models.CharField(max_length=64, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "expires_at"], name="payment_status_expiry_idx")]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.reference} ({self.status})"


class PaymentItem(TimeStampedModel):
    """A line the payment pays for, with the price fixed when the payment started."""

    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    # Orders reject cart lines referenced here while the attempt is pending (see orders.services).
    cart_item = models.ForeignKey(
        "cart.CartItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_items"
    )
    quantity = models.PositiveSmallIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="paymentitem_quantity_positive")
        ]


class WebhookEvent(TimeStampedModel):
    """A gateway webhook that was processed; the unique event id makes redelivery a no-op."""

    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.event_type} {self.event_id}"
