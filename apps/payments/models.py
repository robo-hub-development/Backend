import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import AuditModel, TimeStampedModel


def new_reference() -> str:
    """Public, unguessable payment reference (sent to the gateway and used in the return URL)."""
    return uuid.uuid4().hex


class Payment(AuditModel):
    """One amount an order must receive, by one method. Money movements are PaymentTransaction rows.

    An order has 1-2 payments: `full` (online orders), or for COD a `cod_fee` collected online up front plus
    a `cod_balance` collected in cash on delivery. A failed or expired online payment can be replaced by a new
    one (a retry), but only one online payment per order may be pending at a time.
    """

    class Purpose(models.TextChoices):
        FULL = "full"
        COD_FEE = "cod_fee"  # transit fee for a COD order, paid online
        COD_BALANCE = "cod_balance"  # the item price, paid in cash on delivery

    class Method(models.TextChoices):
        ONLINE = "online"
        CASH = "cash"

    class Status(models.TextChoices):
        PENDING = "pending"
        CAPTURED = "captured"  # online money received
        FAILED = "failed"
        EXPIRED = "expired"
        NEEDS_REFUND = (
            "needs_refund"  # money arrived that cannot be used (wrong amount, order gone): staff refund
        )
        REFUND_IN_PROGRESS = "refund_in_progress"  # refund accepted by the gateway, not yet paid back
        REFUNDED = "refunded"
        COLLECTED = "collected"  # cash received on delivery
        CANCELLED = "cancelled"  # cash payment dropped because the order was cancelled
        UNCOLLECTABLE = "uncollectable"  # cash never collected (return to origin)

    class Flow(models.TextChoices):
        LINK = "link"  # hosted Razorpay Payment Link: the shopper is redirected
        CHECKOUT = "checkout"  # Razorpay Order + Checkout popup on the storefront

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    purpose = models.CharField(max_length=12, choices=Purpose)
    method = models.CharField(max_length=6, choices=Method)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    flow = models.CharField(max_length=8, choices=Flow, blank=True)  # online payments only
    reference = models.CharField(max_length=32, unique=True, default=new_reference, editable=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    gateway_link_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    gateway_order_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    gateway_payment_id = models.CharField(max_length=64, blank=True)
    gateway_refund_id = models.CharField(max_length=64, blank=True)  # set while a refund is in progress
    refund_reason = models.CharField(max_length=200, blank=True)
    short_url = models.URLField(max_length=500, blank=True)
    failure_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["status", "expires_at"], name="payment_status_expiry_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.UniqueConstraint(
                fields=["order"],
                condition=Q(method="online", status="pending"),
                name="payment_one_pending_online_per_order",
            ),
            models.UniqueConstraint(
                fields=["order"], condition=Q(purpose="cod_balance"), name="payment_one_cod_balance_per_order"
            ),
            models.CheckConstraint(
                condition=Q(purpose="cod_balance", method="cash")
                | (~Q(purpose="cod_balance") & Q(method="online")),
                name="payment_purpose_matches_method",
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.purpose} ({self.status})"


class PaymentTransaction(TimeStampedModel):
    """Append-only ledger of money actually moved: a charge received, or a refund paid back.

    Order.amount_paid is always the net of these rows. Rows are never edited or deleted.
    """

    class Kind(models.TextChoices):
        CHARGE = "charge"
        REFUND = "refund"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=6, choices=Kind)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    gateway_ref = models.CharField(
        max_length=64, null=True, blank=True
    )  # gateway payment/refund id; null for cash
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="refunds"
    )  # the charge a refund pays back
    reason = models.CharField(max_length=200, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )  # null: the system

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="paymenttx_amount_positive"),
            models.UniqueConstraint(
                fields=["gateway_ref"],
                condition=Q(gateway_ref__isnull=False),
                name="paymenttx_unique_gateway_ref",
            ),
            models.CheckConstraint(
                condition=Q(kind="charge") | Q(parent__isnull=False), name="paymenttx_refund_has_parent"
            ),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount}"


class WebhookEvent(TimeStampedModel):
    """A gateway webhook that was processed; the unique event id makes redelivery a no-op."""

    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.event_type} {self.event_id}"
