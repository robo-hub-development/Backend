"""Payment lifecycle. The order is the main record; payments hang off it and money is a ledger.

Order.amount_paid / Order.payment_status are summaries of PaymentTransaction rows, rewritten only by
sync_summary. Status changes of the order go through orders.services.transition.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.notifications import services as notifications
from apps.orders import services as order_services
from apps.orders.models import Order

from .gateway import GatewayError, PaymentsNotConfigured, get_gateway, verify_checkout_signature
from .models import Payment, PaymentTransaction, WebhookEvent

logger = logging.getLogger(__name__)

# Payment Link flow: payment_link.*. Checkout (Razorpay Order) flow: payment.authorized / payment.captured /
# order.paid. payment.failed is deliberately absent: one failed try does not end the payment (retry).
WEBHOOK_EVENTS = {
    "payment_link.paid",
    "payment_link.cancelled",
    "payment_link.expired",
    "payment.authorized",
    "payment.captured",
    "order.paid",
    "refund.processed",
    "refund.failed",
}
EXPIRY_GRACE = timedelta(minutes=10)  # wait this long past the due time before giving the stock back
MIN_LINK_LIFETIME = timedelta(minutes=15)  # Razorpay will not create a link that expires sooner


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


def ensure_gateway_configured() -> None:
    """Fail early (before anything is created or held) when online payments cannot work."""
    try:
        get_gateway()
    except PaymentsNotConfigured:
        raise PaymentsUnavailable from None


# --------------------------------------------------------------------------- ledger summary


def sync_summary(order: Order) -> Order:
    """Recompute amount_paid / payment_status from the ledger. The caller holds the order lock."""
    rows = (
        PaymentTransaction.objects.filter(payment__order=order)
        .values("kind")
        .annotate(total=Sum("amount"))
        .values_list("kind", "total")
    )
    totals = dict(rows)
    charged = totals.get(PaymentTransaction.Kind.CHARGE, Decimal(0))
    refunded = totals.get(PaymentTransaction.Kind.REFUND, Decimal(0))
    net = charged - refunded

    if order.payments.filter(status=Payment.Status.REFUND_IN_PROGRESS).exists():
        status = Order.PaymentStatus.REFUND_IN_PROGRESS  # the customer's money is on its way back
    elif refunded and net == 0:
        status = Order.PaymentStatus.REFUNDED
    elif net >= order.total and order.total > 0:
        status = Order.PaymentStatus.PAID
    elif order.status == Order.Status.EXPIRED and charged == 0:
        status = Order.PaymentStatus.FAILED
    else:
        status = Order.PaymentStatus.PENDING  # includes COD: the cash balance is still due
    order.amount_paid = net
    order.payment_status = status
    order.save(update_fields=["amount_paid", "payment_status", "updated_at"])
    return order


def _record_charge(payment: Payment, gateway_ref: str, *, reason="", actor=None) -> PaymentTransaction:
    return PaymentTransaction.objects.create(
        payment=payment,
        kind=PaymentTransaction.Kind.CHARGE,
        amount=payment.amount,
        gateway_ref=gateway_ref or None,
        reason=reason,
        initiated_by=actor,
    )


# --------------------------------------------------------------------------- creating payments


def create_online_payment(order: Order, *, purpose: str, amount: Decimal, flow: str) -> Payment:
    """Open an online payment row for the order; the gateway is contacted by start_gateway_payment."""
    return Payment.objects.create(
        order=order,
        purpose=purpose,
        method=Payment.Method.ONLINE,
        amount=amount,
        flow=flow,
        expires_at=timezone.now() + timedelta(minutes=settings.PAYMENT_LINK_EXPIRY_MINUTES),
        created_by=order.user,
        modified_by=order.user,
    )


def create_cash_payment(order: Order, *, amount: Decimal) -> Payment:
    """The item price a COD customer pays the courier."""
    return Payment.objects.create(
        order=order,
        purpose=Payment.Purpose.COD_BALANCE,
        method=Payment.Method.CASH,
        amount=amount,
        created_by=order.user,
        modified_by=order.user,
    )


def start_gateway_payment(payment: Payment) -> Payment:
    """Create the Razorpay payment link (or Razorpay Order for Checkout) for an online payment.

    Done outside any transaction: a slow or failing gateway must not hold row locks. On failure the payment
    is marked failed and PaymentStartFailed is raised; the order stays as it is for the caller to decide.
    """
    try:
        gateway = get_gateway()
    except PaymentsNotConfigured:
        raise PaymentsUnavailable from None
    order = payment.order
    try:
        if payment.flow == Payment.Flow.CHECKOUT:
            payment.gateway_order_id = gateway.create_order(
                reference=payment.reference, amount_paise=to_paise(payment.amount)
            ).id
            fields = ["gateway_order_id", "updated_at"]
        else:
            link = gateway.create_payment_link(
                reference=payment.reference,
                amount_paise=to_paise(payment.amount),
                expires_at=payment.expires_at,
                customer={
                    "name": order.ship_full_name,
                    "email": order.user.email,
                    "contact": _contact(order.ship_mobile),
                },
            )
            payment.gateway_link_id, payment.short_url = link.id, link.short_url
            fields = ["gateway_link_id", "short_url", "updated_at"]
    except GatewayError:
        fail_payment(payment.pk, Payment.Status.FAILED, "gateway_error")
        raise PaymentStartFailed from None
    payment.save(update_fields=fields)
    return payment


def retry_payment(order: Order, flow: str) -> Payment:
    """Give an unpaid order a fresh online payment (its previous link failed or expired). Order is locked."""
    existing = order.payments.filter(method=Payment.Method.ONLINE, status=Payment.Status.PENDING).first()
    if existing is not None:
        return existing  # idempotent: still waiting on the one the shopper already has
    remaining = order.payment_due_by - timezone.now() if order.payment_due_by else timedelta(0)
    if remaining < MIN_LINK_LIFETIME:
        raise order_services.fail("This order can no longer be paid. Please place a new order.")
    last = order.payments.filter(method=Payment.Method.ONLINE).order_by("-created_at").first()
    purpose = last.purpose if last else Payment.Purpose.FULL
    amount = order.total if purpose == Payment.Purpose.FULL else order.charges_total
    payment = create_online_payment(order, purpose=purpose, amount=amount, flow=flow)
    payment.expires_at = min(payment.expires_at, order.payment_due_by)
    payment.save(update_fields=["expires_at", "updated_at"])
    return payment


# --------------------------------------------------------------------------- outcomes


def fail_payment(payment_id, status: str, reason: str) -> Payment:
    """Close a pending payment as failed/expired. The order and its stock stay as they are (retry)."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status == Payment.Status.PENDING:
            payment.status = status
            payment.failure_reason = reason
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
    return payment


def _needs_refund(payment: Payment, reason: str) -> Payment:
    payment.status = Payment.Status.NEEDS_REFUND
    payment.failure_reason = reason
    payment.save(update_fields=["status", "failure_reason", "gateway_payment_id", "updated_at"])
    logger.error(
        "Payment %s (gateway payment %s) received money that cannot be used (%s): refund it manually.",
        payment.reference,
        payment.gateway_payment_id,
        reason,
    )
    return payment


def settle_captured(payment_id, gateway_payment_id: str, amount_paise: int) -> Payment:
    """Money for an online payment arrived: record it and move the order on. Safe to call repeatedly."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if payment.status in (
            Payment.Status.CAPTURED,
            Payment.Status.REFUND_IN_PROGRESS,
            Payment.Status.REFUNDED,
            Payment.Status.NEEDS_REFUND,
        ):
            return payment  # already settled: a replayed event or a poll racing the webhook
        payment.gateway_payment_id = gateway_payment_id

        if amount_paise != to_paise(payment.amount):
            return _needs_refund(payment, "amount_mismatch")
        if order.status == Order.Status.EXPIRED:
            # Paid after the window closed and the stock was released: take it again if it is still there.
            try:
                with transaction.atomic():
                    order_services.take_stock([(i.product_id, i.quantity) for i in order.items.all()])
            except ValidationError:
                return _needs_refund(payment, "out_of_stock")
            if order.items.filter(cart_item__ordered_at__isnull=False).exists():
                order_services.return_order_stock(order)
                return _needs_refund(payment, "cart_changed")
        elif order.status != Order.Status.AWAITING_PAYMENT:
            return _needs_refund(payment, f"order_{order.status}")

        _record_charge(payment, gateway_payment_id, reason="payment captured")
        payment.status = Payment.Status.CAPTURED
        payment.failure_reason = ""
        payment.save(update_fields=["status", "failure_reason", "gateway_payment_id", "updated_at"])
        # Any other open link for this order would only ever produce a duplicate payment.
        order.payments.filter(method=Payment.Method.ONLINE, status=Payment.Status.PENDING).update(
            status=Payment.Status.FAILED, failure_reason="superseded", updated_at=timezone.now()
        )
        online = order.payment_method == Order.PaymentMethod.ONLINE
        target = Order.Status.CONFIRMED if online else Order.Status.PENDING
        order_services.transition(order, target, reason="payment captured")
        order_services.commit_cart_lines(order)
        sync_summary(order)
        notifications.after_commit(notifications.notify_order_confirmed, order.pk)
    return payment


def release_unpaid(order: Order, to_status: str, *, actor=None, reason: str = "") -> Order:
    """An order that was never paid is cancelled or expires: return its stock and close its open payments.

    The order must be locked and in AWAITING_PAYMENT. Its cart lines were never marked ordered, so the cart
    is intact and the shopper can check out again.
    """
    order_services.return_order_stock(order)
    closed = Payment.Status.EXPIRED if to_status == Order.Status.EXPIRED else Payment.Status.FAILED
    order.payments.filter(status=Payment.Status.PENDING, method=Payment.Method.ONLINE).update(
        status=closed, failure_reason=reason[:40], updated_at=timezone.now()
    )
    order.payments.filter(status=Payment.Status.PENDING, method=Payment.Method.CASH).update(
        status=Payment.Status.CANCELLED, updated_at=timezone.now()
    )
    order_services.transition(order, to_status, actor=actor, reason=reason)
    return sync_summary(order)


# --------------------------------------------------------------------------- cash on delivery


def collect_cash(order: Order, *, actor=None) -> None:
    """The courier handed over the cash balance. The order is locked."""
    payment = (
        order.payments.select_for_update()
        .filter(purpose=Payment.Purpose.COD_BALANCE, status=Payment.Status.PENDING)
        .first()
    )
    if payment is None:
        return
    _record_charge(payment, "", reason="cash collected on delivery", actor=actor)
    payment.status = Payment.Status.COLLECTED
    payment.save(update_fields=["status", "updated_at"])
    sync_summary(order)


def close_cash(order: Order, status: str) -> None:
    """The cash balance will never be collected (order cancelled, or the customer refused delivery)."""
    order.payments.filter(purpose=Payment.Purpose.COD_BALANCE, status=Payment.Status.PENDING).update(
        status=status, updated_at=timezone.now()
    )


# --------------------------------------------------------------------------- refunds


def refund_order(order: Order, *, actor=None, reason: str = "") -> None:
    """Refund every captured online payment of an order that was just cancelled before dispatch.

    Runs after the cancellation is committed and never raises: a refund that fails is logged for staff to do
    by hand and the payment stays "captured", so it is visible and can be retried.
    """
    for payment in order.payments.filter(method=Payment.Method.ONLINE, status=Payment.Status.CAPTURED):
        try:
            refund_id = get_gateway().refund(payment.gateway_payment_id, to_paise(payment.amount))
        except (GatewayError, PaymentsNotConfigured):
            logger.error(
                "Refund failed for order %s (payment %s): refund it manually.",
                order.order_number,
                payment.gateway_payment_id,
            )
            continue
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            # Accepted, not yet paid back: the ledger only records the refund once the gateway confirms it.
            Payment.objects.filter(pk=payment.pk).update(
                status=Payment.Status.REFUND_IN_PROGRESS,
                gateway_refund_id=refund_id,
                refund_reason=reason[:200],
                updated_at=timezone.now(),
            )
            sync_summary(locked)
            notifications.after_commit(notifications.notify_refund_started, payment.pk)


def complete_refund(payment_id) -> Payment:
    """The gateway confirmed the refund: write it to the ledger and mark the payment (and order) refunded."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status != Payment.Status.REFUND_IN_PROGRESS:
            return payment  # already completed (duplicate webhook / reconcile) or not refunding
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        PaymentTransaction.objects.create(
            payment=payment,
            kind=PaymentTransaction.Kind.REFUND,
            amount=payment.amount,
            gateway_ref=payment.gateway_refund_id,
            parent=payment.transactions.filter(kind=PaymentTransaction.Kind.CHARGE).first(),
            reason=payment.refund_reason,
        )
        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=["status", "updated_at"])
        sync_summary(order)
        notifications.after_commit(notifications.notify_refund_completed, payment.pk)
        return payment


def fail_refund(payment_id) -> Payment:
    """The gateway could not pay the refund back: the payment is "captured" again so staff can retry it."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status != Payment.Status.REFUND_IN_PROGRESS:
            return payment
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        logger.error(
            "Refund %s for order %s (payment %s) failed at the gateway: refund it manually.",
            payment.gateway_refund_id,
            order.order_number,
            payment.gateway_payment_id,
        )
        payment.status = Payment.Status.CAPTURED
        payment.save(update_fields=["status", "updated_at"])
        sync_summary(order)
        return payment


def reconcile_refund(payment: Payment) -> Payment:
    """Ask the gateway where a refund stands (its webhook may be late or lost) and apply the answer."""
    if payment.status != Payment.Status.REFUND_IN_PROGRESS or not payment.gateway_refund_id:
        return payment
    try:
        state = get_gateway().fetch_refund(payment.gateway_refund_id)
    except (GatewayError, PaymentsNotConfigured):
        logger.warning("Could not check refund %s with the gateway.", payment.gateway_refund_id)
        return payment
    if state == "processed":
        return complete_refund(payment.pk)
    if state == "failed":
        return fail_refund(payment.pk)
    return payment


def reconcile_refunds() -> int:
    """Settle every refund still in progress; returns how many reached a final state."""
    settled = 0
    for payment in Payment.objects.filter(status=Payment.Status.REFUND_IN_PROGRESS):
        if reconcile_refund(payment).status != Payment.Status.REFUND_IN_PROGRESS:
            settled += 1
    return settled


# --------------------------------------------------------------------------- webhooks and reconciliation


def _entity(entities: dict, name: str) -> dict:
    return (entities.get(name) or {}).get("entity") or {}


def _find_payment(entities: dict, *, lock: bool):
    """The payment a webhook is about: by payment-link reference, or by the Razorpay order id (Checkout)."""
    link = _entity(entities, "payment_link")
    payment, order = _entity(entities, "payment"), _entity(entities, "order")
    queryset = Payment.objects.select_for_update() if lock else Payment.objects
    if link.get("reference_id"):
        found = queryset.filter(reference=link["reference_id"]).first()
        if found and found.gateway_link_id and found.gateway_link_id != link.get("id"):
            return None  # same reference but a different link: not ours
        return found
    order_id = payment.get("order_id") or order.get("id")
    return queryset.filter(gateway_order_id=order_id).first() if order_id else None


def _capture_authorized(event_id: str, entities: dict) -> str:
    """payment.authorized (account without auto-capture): capture exactly the amount we expect.

    The capture then triggers payment.captured, which is what settles the payment. A failing capture call
    raises, so the webhook answers 5xx and Razorpay delivers it again.
    """
    if WebhookEvent.objects.filter(event_id=event_id).exists():
        return "duplicate"
    raw = _entity(entities, "payment")
    payment = _find_payment(entities, lock=False)
    result = "unknown"
    if payment is not None:
        result = "ignored"
        amount = int(raw.get("amount", 0))
        open_states = (Payment.Status.PENDING, Payment.Status.FAILED, Payment.Status.EXPIRED)
        if payment.status not in open_states:
            logger.info("Authorized payment %s for a settled payment; not captured.", raw.get("id"))
        elif amount != to_paise(payment.amount):
            logger.error("Authorized amount for payment %s does not match; not captured.", payment.reference)
        else:
            get_gateway().capture(str(raw.get("id", "")), amount)
            result = "processed"
    WebhookEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": "payment.authorized"})
    return result


def _handle_refund_event(event_id: str, event_type: str, entities: dict) -> str:
    with transaction.atomic():
        _, created = WebhookEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type}
        )
        if not created:
            return "duplicate"
        refund_id = str(_entity(entities, "refund").get("id", ""))
        payment = Payment.objects.filter(gateway_refund_id=refund_id).first() if refund_id else None
        if payment is None:
            logger.warning("Webhook %s does not match any refund; ignored.", event_type)
            return "unknown"
        if event_type == "refund.processed":
            complete_refund(payment.pk)
        else:
            fail_refund(payment.pk)
    return "processed"


def handle_webhook(event_id: str, payload: dict) -> str:
    """Apply one verified webhook. Returns processed | duplicate | ignored | unknown."""
    event_type = payload.get("event", "")
    if event_type not in WEBHOOK_EVENTS:
        return "ignored"  # e.g. payment.failed: one failed try does not end the payment, the shopper retries
    entities = payload.get("payload") or {}
    if event_type == "payment.authorized":
        return _capture_authorized(event_id, entities)
    if event_type in ("refund.processed", "refund.failed"):
        return _handle_refund_event(event_id, event_type, entities)

    with transaction.atomic():
        # Recording the event and applying it share one transaction: if applying fails, the event is not
        # recorded and the gateway's retry is processed again.
        _, created = WebhookEvent.objects.get_or_create(
            event_id=event_id, defaults={"event_type": event_type}
        )
        if not created:
            return "duplicate"
        payment = _find_payment(entities, lock=True)
        if payment is None:
            logger.warning("Webhook %s does not match any payment; ignored.", event_type)
            return "unknown"
        if event_type == "payment_link.cancelled":
            fail_payment(payment.pk, Payment.Status.FAILED, "cancelled")
        elif event_type == "payment_link.expired":
            fail_payment(payment.pk, Payment.Status.EXPIRED, "expired")
        else:  # payment_link.paid, payment.captured, order.paid
            raw = _entity(entities, "payment")
            settle_captured(payment.pk, str(raw.get("id", "")), int(raw.get("amount", 0)))
    return "processed"


def reconcile(payment: Payment) -> Payment:
    """Ask the gateway about a pending payment (the webhook may be late or lost) and apply the answer."""
    if payment.status != Payment.Status.PENDING or payment.method != Payment.Method.ONLINE:
        return payment
    try:
        gateway = get_gateway()
        if payment.gateway_link_id:
            remote = gateway.fetch_payment_link(payment.gateway_link_id)
        elif payment.gateway_order_id:
            remote = gateway.fetch_order(payment.gateway_order_id)
        else:
            return payment
    except (GatewayError, PaymentsNotConfigured):
        logger.warning("Could not reconcile payment %s with the gateway.", payment.reference)
        return payment
    if remote.status == "paid":
        settle_captured(payment.pk, remote.payment_id, remote.amount_paid_paise)
    elif remote.status in ("cancelled", "expired"):
        closed = Payment.Status.EXPIRED if remote.status == "expired" else Payment.Status.FAILED
        fail_payment(payment.pk, closed, remote.status)
    payment.refresh_from_db()
    return payment


def verify_checkout(user, reference: str, order_id: str, payment_id: str, signature: str) -> Payment:
    """Check the signature Checkout hands the browser, then settle from what the gateway itself reports.

    The signature only proves that this payment id belongs to this Razorpay order; whether money was
    captured is always taken from the gateway, never from the browser.
    """
    payment = (
        Payment.objects.select_related("order")
        .filter(order__user=user, reference=reference, flow=Payment.Flow.CHECKOUT, gateway_order_id=order_id)
        .first()
    )
    if payment is None:
        raise NotFound
    if not verify_checkout_signature(order_id, payment_id, signature):
        raise ValidationError({"razorpaySignature": "Invalid payment signature."})
    return reconcile(payment)


def expire_unpaid_orders() -> int:
    """Expire orders past their payment window (after one last look at the gateway), freeing their stock."""
    cutoff = timezone.now() - EXPIRY_GRACE
    expired = 0
    stale = Order.objects.filter(status=Order.Status.AWAITING_PAYMENT, payment_due_by__lt=cutoff)
    for order in stale:
        for payment in order.payments.filter(method=Payment.Method.ONLINE, status=Payment.Status.PENDING):
            reconcile(payment)
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status != Order.Status.AWAITING_PAYMENT:
                continue  # it was paid (or cancelled) while we were checking
            release_unpaid(locked, Order.Status.EXPIRED, reason="payment window elapsed")
            expired += 1
    return expired
