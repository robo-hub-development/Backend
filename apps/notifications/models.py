from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel


class EmailLog(TimeStampedModel):
    """One transactional email to a customer: what, to whom, and whether it went out.

    `dedupe_key` makes an event send at most once however often it fires (a replayed webhook, a retry): the
    row is claimed before sending, so a second attempt finds it and does nothing. Failed rows are retried."""

    class Kind(models.TextChoices):
        ORDER_CONFIRMATION = "order_confirmation", "Order confirmation"
        ORDER_SHIPPED = "order_shipped", "Order shipped"
        ORDER_DELIVERED = "order_delivered", "Order delivered"
        TRACKING_ASSIGNED = "tracking_assigned", "Delivery details"
        INVOICE = "invoice", "Invoice"
        REFUND_STARTED = "refund_started", "Refund started"
        REFUND_COMPLETED = "refund_completed", "Refund completed"

    class Status(models.TextChoices):
        PENDING = "pending"  # claimed, being sent
        SENT = "sent"
        FAILED = "failed"

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="emails")
    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )  # the payment a refund email is about
    kind = models.CharField(max_length=20, choices=Kind)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=7, choices=Status, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)  # exception type and a short message, never content
    sent_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=80, null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"],
                condition=Q(dedupe_key__isnull=False),
                name="emaillog_unique_dedupe_key",
            ),
        ]
        indexes = [models.Index(fields=["status", "attempts"], name="emaillog_status_attempts_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} to {self.recipient} ({self.status})"
