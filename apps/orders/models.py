from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.common.models import AuditModel, TimeStampedModel

ORDER_NUMBER_PREFIX = "SRH-"
ORDER_NUMBER_SEQUENCE = "orders_order_number_seq"  # Postgres sequence created in 0001; starts at 1001


class Order(AuditModel):
    """A placed order. Prices and the address are snapshots: later catalogue/profile edits never alter it."""

    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment"  # created, online money still due (full price, or the COD fee)
        PENDING = (
            "pending"  # paid what was due online; waiting for staff to accept (COD: cash due on delivery)
        )
        CONFIRMED = "confirmed"
        SHIPPED = "shipped"
        DELIVERED = "delivered"
        CANCELLED = "cancelled"
        EXPIRED = "expired"  # never paid in time; stock was released
        RTO = "rto"  # return to origin: the customer refused delivery

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Cash on delivery"
        ONLINE = "online", "Online"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending"
        PAID = "paid"
        FAILED = "failed"
        REFUND_IN_PROGRESS = "refund_in_progress"  # cancelled; the gateway has accepted the refund
        REFUNDED = "refunded"

    # Allowed status moves; every writer (customer cancel, staff actions) must go through this map.
    TRANSITIONS = {
        Status.AWAITING_PAYMENT: {Status.PENDING, Status.CONFIRMED, Status.CANCELLED, Status.EXPIRED},
        Status.PENDING: {Status.CONFIRMED, Status.CANCELLED},
        Status.CONFIRMED: {Status.SHIPPED, Status.CANCELLED},
        Status.SHIPPED: {Status.DELIVERED, Status.RTO},
        Status.DELIVERED: set(),
        Status.CANCELLED: set(),
        # Only a payment arriving after expiry may revive an order (payments.services.settle_captured).
        Status.EXPIRED: {Status.PENDING, Status.CONFIRMED},
        Status.RTO: set(),
    }
    # Orders that will never be fulfilled: nothing more is owed on them.
    DEAD_STATUSES = frozenset({Status.CANCELLED, Status.EXPIRED, Status.RTO})

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    number = models.PositiveIntegerField(unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    payment_method = models.CharField(max_length=6, choices=PaymentMethod)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus, default=PaymentStatus.PENDING)
    # Money. total = subtotal (items) + charges_total (OrderCharge rows, e.g. the COD transit fee).
    # amount_paid is the net of the payment ledger (charges - refunds), kept in sync by payments.services.
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    charges_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_due_by = models.DateTimeField(null=True, blank=True, help_text="Unpaid orders expire after this.")
    # The cart this order was placed from (null for orders created without one, e.g. direct item lists).
    cart = models.ForeignKey(
        "cart.Cart", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Entered by staff once the parcel is handed to the delivery partner; the customer uses it to follow the
    # shipment on the partner's own tracking page (no courier integration).
    courier_name = models.CharField(max_length=100, blank=True, help_text="Delivery partner, e.g. Delhivery.")
    tracking_number = models.CharField(
        max_length=64, blank=True, help_text="The delivery partner's reference / AWB number."
    )

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
        indexes = [
            models.Index(fields=["user", "-created_at"], name="order_user_recent_idx"),
            models.Index(fields=["status", "payment_due_by"], name="order_status_due_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(total__gte=0), name="order_total_non_negative"),
            models.CheckConstraint(
                condition=Q(total=F("subtotal") + F("charges_total")),
                name="order_total_is_subtotal_plus_charges",
            ),
            models.CheckConstraint(
                condition=Q(amount_paid__gte=0) & Q(amount_paid__lte=F("total")),
                name="order_amount_paid_in_range",
            ),
        ]

    def __str__(self):
        return self.order_number

    @property
    def order_number(self) -> str:
        return f"{ORDER_NUMBER_PREFIX}{self.number}"

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.TRANSITIONS[self.status]

    @property
    def amount_due(self):
        """What the customer still owes: online money while awaiting payment, cash on delivery for COD."""
        return 0 if self.status in self.DEAD_STATUSES else self.total - self.amount_paid

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
    # The cart line this item came from, when there was one (the line is kept, marked ordered).
    cart_item = models.ForeignKey(
        "cart.CartItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="order_items"
    )
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


class OrderCharge(TimeStampedModel):
    """A priced line on top of the items (today only the COD transit fee). Frozen when the order is placed."""

    class Kind(models.TextChoices):
        COD_TRANSIT = "cod_transit"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="charges")
    kind = models.CharField(max_length=20, choices=Kind)
    label = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    rule = models.JSONField(default=dict, blank=True, help_text="The pricing rule that produced this amount.")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ordercharge_amount_positive"),
            models.UniqueConstraint(fields=["order", "kind"], name="ordercharge_one_per_kind"),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount}"


class OrderStatusEvent(TimeStampedModel):
    """Audit trail: every status change, who made it and why. Written only by orders.services.transition."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=20, blank=True)  # empty for the creation event
    to_status = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )  # null: the system (payment webhook, expiry job)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["order", "created_at"], name="orderevent_order_time_idx")]

    def __str__(self):
        return f"{self.from_status or '-'} -> {self.to_status}"


class CodTransitPolicy(AuditModel):
    """Staff-editable rule for the fee charged online on cash-on-delivery orders (to cover transit).

    The most specific matching rule wins: pincode prefix (longer first) > state > default. With no matching
    active rule the fee is zero. The result is copied into OrderCharge, so editing a rule never changes
    existing orders.
    """

    class Scope(models.TextChoices):
        DEFAULT = "default"
        STATE = "state"
        PINCODE_PREFIX = "pincode_prefix"

    class Mode(models.TextChoices):
        FIXED = "fixed"  # `value` rupees
        PERCENT = "percent"  # `value` % of the items subtotal

    scope = models.CharField(max_length=14, choices=Scope, default=Scope.DEFAULT)
    match = models.CharField(
        max_length=100,
        blank=True,
        help_text="State name, or the first 1-6 digits of a pincode. Empty for default.",
    )
    mode = models.CharField(max_length=8, choices=Mode, default=Mode.FIXED)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_order_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "COD transit policies"
        ordering = ["scope", "match", "-effective_from"]
        indexes = [models.Index(fields=["is_active", "scope"], name="codpolicy_active_scope_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(value__gte=0), name="codpolicy_value_non_negative"),
            models.CheckConstraint(
                condition=Q(mode="fixed") | Q(value__lte=100), name="codpolicy_percent_at_most_100"
            ),
            models.CheckConstraint(
                condition=(Q(scope="default") & Q(match="")) | (~Q(scope="default") & ~Q(match="")),
                name="codpolicy_match_fits_scope",
            ),
            models.CheckConstraint(
                condition=Q(min_fee__isnull=True) | Q(max_fee__isnull=True) | Q(min_fee__lte=F("max_fee")),
                name="codpolicy_fee_band_ordered",
            ),
            models.CheckConstraint(
                condition=Q(min_order_value__isnull=True)
                | Q(max_order_value__isnull=True)
                | Q(min_order_value__lte=F("max_order_value")),
                name="codpolicy_order_value_band_ordered",
            ),
        ]

    def __str__(self):
        target = self.match or "everywhere"
        amount = f"{self.value}%" if self.mode == self.Mode.PERCENT else f"Rs {self.value}"
        return f"COD fee {amount} ({self.scope}: {target})"

    def clean(self):
        super().clean()
        errors = {}
        self.match = self.match.strip()
        if self.scope == self.Scope.DEFAULT:
            self.match = ""
        elif not self.match:
            errors["match"] = "Required for this scope."
        elif self.scope == self.Scope.PINCODE_PREFIX and not (self.match.isdigit() and len(self.match) <= 6):
            errors["match"] = "Use 1-6 digits."
        if errors:
            raise ValidationError(errors)
