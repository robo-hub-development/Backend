import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.orders import services as order_services
from apps.orders.models import Order
from apps.orders.services import Purchase

from .gateway import GatewayError, PaymentsNotConfigured, get_gateway
from .models import PaymentAttempt, PaymentItem, WebhookEvent

logger = logging.getLogger(__name__)

WEBHOOK_EVENTS = {"payment_link.paid", "payment_link.cancelled", "payment_link.expired"}
EXPIRY_GRACE = timedelta(minutes=10)  # wait this long past expiry before giving the stock back


class PaymentsUnavailable(APIException):
    status_code = 503
    default_detail = "Online payments are not available right now."
    default_code = "payments_unavailable"


class PaymentStartFailed(APIException):
    status_code = 502
    default_detail = "Could not start the payment. Please try again."
    default_code = "payment_start_failed"


def to_paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def _contact(mobile: str) -> str:
    digits = mobile.lstrip("+")
    return f"+91{digits}" if len(digits) == 10 else f"+{digits}"  # Razorpay wants a country code


def start_online_payment(user, cart_id, cart_item_ids, address: dict) -> PaymentAttempt:
    """Hold the chosen cart lines and stock, then open a payment link. No order exists until it is paid."""
    try:
        gateway = get_gateway()
    except PaymentsNotConfigured:
        raise PaymentsUnavailable from None

    with transaction.atomic():
        cart, lines = order_services.lock_cart_lines(user, cart_id, cart_item_ids)
        products = order_services.take_stock([(line.product_id, line.quantity) for line in lines])
        total = sum((products[line.product_id].price * line.quantity for line in lines), Decimal(0))
        if total <= 0:
            raise ValidationError({"detail": "The order total must be greater than zero."})
        attempt = PaymentAttempt.objects.create(
            user=user,
            cart=cart,
            amount=total,
            address=address,
            expires_at=timezone.now() + timedelta(minutes=settings.PAYMENT_LINK_EXPIRY_MINUTES),
            created_by=user,
            modified_by=user,
        )
        PaymentItem.objects.bulk_create(
            PaymentItem(
                attempt=attempt,
                product=products[line.product_id],
                cart_item=line,
                quantity=line.quantity,
                unit_price=products[line.product_id].price,
            )
            for line in lines
        )

    # The gateway call happens outside the transaction: a slow or failing gateway must not hold row locks.
    customer = {"name": address["full_name"], "email": user.email, "contact": _contact(address["mobile"])}
    try:
        link = gateway.create_payment_link(
            reference=attempt.reference,
            amount_paise=to_paise(attempt.amount),
            expires_at=attempt.expires_at,
            customer=customer,
        )
    except GatewayError:
        fail_attempt(attempt.pk, "gateway_error")
        raise PaymentStartFailed from None

    attempt.gateway_link_id = link.id
    attempt.short_url = link.short_url
    attempt.save(update_fields=["gateway_link_id", "short_url", "updated_at"])
    return attempt


def fail_attempt(attempt_id, reason: str) -> PaymentAttempt:
    """Mark a pending attempt failed and give its reserved stock back. No-op unless still pending."""
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status != PaymentAttempt.Status.PENDING:
            return attempt
        order_services.return_stock([(item.product_id, item.quantity) for item in attempt.items.all()])
        attempt.status = PaymentAttempt.Status.FAILED
        attempt.failure_reason = reason
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
    return attempt


def _needs_refund(attempt: PaymentAttempt, reason: str) -> PaymentAttempt:
    attempt.status = PaymentAttempt.Status.NEEDS_REFUND
    attempt.failure_reason = reason
    attempt.save(update_fields=["status", "failure_reason", "gateway_payment_id", "updated_at"])
    logger.error(
        "Payment %s (gateway payment %s) was paid but cannot be fulfilled (%s): refund it manually.",
        attempt.reference,
        attempt.gateway_payment_id,
        reason,
    )
    return attempt


def finalise_paid(attempt_id, gateway_payment_id: str, amount_paise: int) -> PaymentAttempt:
    """Turn a successful payment into exactly one confirmed order. Safe to call any number of times."""
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
        if attempt.status in (PaymentAttempt.Status.PAID, PaymentAttempt.Status.NEEDS_REFUND):
            return attempt  # already settled: a replayed event or a poll racing the webhook

        items = list(attempt.items.select_related("product", "cart_item"))
        pairs = [(item.product_id, item.quantity) for item in items]
        was_pending = attempt.status == PaymentAttempt.Status.PENDING
        attempt.gateway_payment_id = gateway_payment_id

        if amount_paise != to_paise(attempt.amount):
            if was_pending:
                order_services.return_stock(pairs)
            return _needs_refund(attempt, "amount_mismatch")

        if not was_pending:
            # It failed/expired earlier and its stock was released; a late payment must take stock again.
            if any(item.cart_item and item.cart_item.ordered_at for item in items):
                return _needs_refund(attempt, "cart_changed")
            try:
                with transaction.atomic():
                    order_services.take_stock(pairs)
            except ValidationError:
                return _needs_refund(attempt, "out_of_stock")

        order = order_services.create_order(
            user=attempt.user,
            cart=attempt.cart,
            purchases=[Purchase(i.product, i.quantity, i.unit_price, i.cart_item) for i in items],
            address=attempt.address,
            payment_method=Order.PaymentMethod.ONLINE,
            payment_status=Order.PaymentStatus.PAID,
            status=Order.Status.CONFIRMED,
        )
        attempt.order = order
        attempt.status = PaymentAttempt.Status.PAID
        attempt.failure_reason = ""
        attempt.save(update_fields=["order", "status", "failure_reason", "gateway_payment_id", "updated_at"])
    return attempt


def handle_webhook(event_id: str, payload: dict) -> str:
    """Apply one verified webhook. Returns processed | duplicate | ignored | unknown."""
    event_type = payload.get("event", "")
    if event_type not in WEBHOOK_EVENTS:
        return "ignored"  # e.g. payment.failed: one failed try does not end the link, the shopper can retry
    entities = payload.get("payload") or {}
    link = (entities.get("payment_link") or {}).get("entity") or {}

    with transaction.atomic():
        # Recording the event and applying it share one transaction: if applying fails, the event is not
        # recorded and the gateway's retry is processed again.
        _, created = WebhookEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type}
        )
        if not created:
            return "duplicate"
        attempt = (
            PaymentAttempt.objects.select_for_update().filter(reference=link.get("reference_id", "")).first()
        )
        if attempt is None or (attempt.gateway_link_id and attempt.gateway_link_id != link.get("id")):
            logger.warning("Webhook %s does not match any payment; ignored.", event_type)
            return "unknown"
        if event_type == "payment_link.paid":
            payment = (entities.get("payment") or {}).get("entity") or {}
            finalise_paid(attempt.pk, str(payment.get("id", "")), int(payment.get("amount", 0)))
        elif event_type == "payment_link.cancelled":
            fail_attempt(attempt.pk, "cancelled")
        else:
            fail_attempt(attempt.pk, "expired")
    return "processed"


def reconcile(attempt: PaymentAttempt) -> PaymentAttempt:
    """Ask the gateway about a pending payment (the webhook may be late or lost) and apply the answer."""
    if attempt.status != PaymentAttempt.Status.PENDING or not attempt.gateway_link_id:
        return attempt
    try:
        link = get_gateway().fetch_payment_link(attempt.gateway_link_id)
    except (GatewayError, PaymentsNotConfigured):
        logger.warning("Could not reconcile payment %s with the gateway.", attempt.reference)
        return attempt
    if link.status == "paid":
        finalise_paid(attempt.pk, link.payment_id, link.amount_paid_paise)
    elif link.status in ("cancelled", "expired"):
        fail_attempt(attempt.pk, link.status)
    attempt.refresh_from_db()
    return attempt


def expire_stale_attempts() -> int:
    """Release stock held by attempts that outlived their link (after one last look at the gateway)."""
    cutoff = timezone.now() - EXPIRY_GRACE
    expired = 0
    for attempt in PaymentAttempt.objects.filter(status=PaymentAttempt.Status.PENDING, expires_at__lt=cutoff):
        attempt = reconcile(attempt)
        if attempt.status == PaymentAttempt.Status.PENDING:
            fail_attempt(attempt.pk, "expired")
            expired += 1
    return expired


def refund_cancelled_order(order: Order) -> None:
    """Refund the online payment of an order that was just cancelled. Never raises: the cancellation stands,
    and a refund that fails is logged for staff to do by hand (the order keeps payment status "paid")."""
    attempt = PaymentAttempt.objects.filter(order=order, status=PaymentAttempt.Status.PAID).first()
    if attempt is None or attempt.refund_id:
        return
    try:
        refund_id = get_gateway().refund(attempt.gateway_payment_id, to_paise(attempt.amount))
    except (GatewayError, PaymentsNotConfigured):
        logger.error(
            "Refund failed for order %s (payment %s): refund it manually.",
            order.order_number,
            attempt.gateway_payment_id,
        )
        return
    now = timezone.now()
    with transaction.atomic():
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            refund_id=refund_id, refunded_at=now, updated_at=now
        )
        Order.objects.filter(pk=order.pk).update(payment_status=Order.PaymentStatus.REFUNDED, updated_at=now)
