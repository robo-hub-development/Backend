"""Transactional emails for order events. Rules (see .claude/rules/email-notifications.md):

- Email is a side effect of a business event, never part of it: every sender runs after the database commit
  (`after_commit`), and none of them ever raises into the caller. A mail problem is logged and the row in
  EmailLog is marked failed, to be retried by `manage.py retry_failed_emails`.
- Each event is sent once: the EmailLog row is claimed (unique dedupe_key) before sending, so replayed
  webhooks and retried jobs cannot email the customer twice.
- The message is built from the database at send time (not from data captured earlier), and the log stores no
  email content.
"""

import hashlib
import logging
from functools import partial

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailLog

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
Kind = EmailLog.Kind


# --------------------------------------------------------------------------- public events


def after_commit(func, *args) -> None:
    """Run `func(*args)` once the surrounding transaction commits (at once outside one). Errors are logged and
    never raised, so a mail problem cannot fail the request or webhook that triggered it."""
    transaction.on_commit(partial(func, *args), robust=True)


def notify_order_confirmed(order_id) -> None:
    """The order is real: placed with nothing due online, or its online payment just arrived."""
    _notify(Kind.ORDER_CONFIRMATION, order_id, dedupe_key=f"{Kind.ORDER_CONFIRMATION}:{order_id}")


def notify_refund_started(payment_id) -> None:
    """The gateway accepted a refund for this payment (the customer cancelled a paid order)."""
    _notify_payment(Kind.REFUND_STARTED, payment_id)


def notify_refund_completed(payment_id) -> None:
    """The gateway confirmed the refund went back to the customer."""
    _notify_payment(Kind.REFUND_COMPLETED, payment_id)


def notify_order_shipped(order_id) -> None:
    """The order went to the delivery partner: tell the customer, with the partner and tracking number."""
    _notify(Kind.ORDER_SHIPPED, order_id, dedupe_key=f"{Kind.ORDER_SHIPPED}:{order_id}")


def notify_order_delivered(order_id) -> None:
    """The order reached the customer (COD cash collected, nothing left to pay)."""
    _notify(Kind.ORDER_DELIVERED, order_id, dedupe_key=f"{Kind.ORDER_DELIVERED}:{order_id}")


def notify_tracking_assigned(order_id) -> None:
    """Staff assigned (or corrected) the delivery partner / tracking number. Each distinct set of details is
    sent once: saving the same values again does not email again, a corrected number does."""
    from apps.orders.models import Order

    order = Order.objects.only("courier_name", "tracking_number", "status").filter(pk=order_id).first()
    if order is None or order.status in Order.DEAD_STATUSES:
        return  # nothing will be delivered
    if not (order.courier_name or order.tracking_number):
        return  # details were cleared: nothing to tell the customer
    digest = hashlib.sha256(f"{order.courier_name}|{order.tracking_number}".encode()).hexdigest()[:12]
    _notify(Kind.TRACKING_ASSIGNED, order_id, dedupe_key=f"{Kind.TRACKING_ASSIGNED}:{order_id}:{digest}")


def send_invoice(order_id, *, resend: bool = False) -> EmailLog | None:
    """Email the invoice. Automatic sends happen once (when the order ships); `resend=True` is for staff."""
    key = None if resend else f"{Kind.INVOICE}:{order_id}"
    return _notify(Kind.INVOICE, order_id, dedupe_key=key)


def retry_failed(limit: int = 100) -> int:
    """Send again every email that failed (up to MAX_ATTEMPTS tries each); returns how many went out."""
    sent = 0
    for log in EmailLog.objects.filter(status=EmailLog.Status.FAILED, attempts__lt=MAX_ATTEMPTS)[:limit]:
        sent += deliver(log).status == EmailLog.Status.SENT
    return sent


# --------------------------------------------------------------------------- internals


def _notify_payment(kind: str, payment_id) -> None:
    from apps.payments.models import Payment

    try:
        payment = Payment.objects.only("order_id").get(pk=payment_id)
    except Payment.DoesNotExist:
        return
    _notify(kind, payment.order_id, payment_id=payment_id, dedupe_key=f"{kind}:{payment_id}")


def _notify(kind: str, order_id, *, payment_id=None, dedupe_key: str | None) -> EmailLog | None:
    """Claim the event, then send. Never raises."""
    if not settings.NOTIFICATIONS_ENABLED:
        return None
    try:
        from apps.orders.models import Order

        order = Order.objects.select_related("user").get(pk=order_id)
        log = _claim(order, kind, payment_id, dedupe_key)
        return deliver(log) if log is not None else None
    except Exception:
        logger.exception("Could not queue %s email for order %s", kind, order_id)
        return None


def _claim(order, kind: str, payment_id, dedupe_key: str | None) -> EmailLog | None:
    """Create the log row; None when this event was already claimed (nothing to do)."""
    try:
        with transaction.atomic():
            return EmailLog.objects.create(
                order=order,
                payment_id=payment_id,
                kind=kind,
                recipient=order.user.email,
                dedupe_key=dedupe_key,
            )
    except IntegrityError:
        return None


def deliver(log: EmailLog) -> EmailLog:
    """Build and send the email for a log row, recording the outcome. Never raises."""
    log.attempts += 1
    try:
        message = _build(log)
        log.subject = message.subject
        message.send(fail_silently=False)
    except Exception as exc:
        log.status = EmailLog.Status.FAILED
        log.error = f"{type(exc).__name__}: {exc}"[:200]
        logger.exception("Email %s for order %s failed (attempt %s)", log.kind, log.order_id, log.attempts)
    else:
        log.status, log.error, log.sent_at = EmailLog.Status.SENT, "", timezone.now()
    log.save(update_fields=["subject", "status", "error", "sent_at", "attempts", "updated_at"])
    return log


SUBJECTS = {
    Kind.ORDER_CONFIRMATION: "We got your order {order}",
    Kind.ORDER_SHIPPED: "Your order {order} is on its way",
    Kind.ORDER_DELIVERED: "Your order {order} has been delivered",
    Kind.TRACKING_ASSIGNED: "Delivery details for your order {order}",
    Kind.INVOICE: "Your invoice {invoice} for order {order}",
    Kind.REFUND_STARTED: "Your refund for order {order} has started",
    Kind.REFUND_COMPLETED: "Your refund for order {order} is complete",
}


def _build(log: EmailLog) -> EmailMultiAlternatives:
    from apps.orders.invoice import invoice_context
    from apps.orders.models import Order

    order = Order.objects.select_related("user").prefetch_related("items", "charges").get(pk=log.order_id)
    invoice = invoice_context(order)
    payment = log.payment
    context = {
        "order": order,
        "invoice": invoice,
        "seller": settings.SELLER,
        "order_url": f"{settings.SITE_URL.rstrip('/')}/account/orders/{order.pk}",
        "customer_name": order.ship_full_name or order.user.name,
        "payment": payment,
        "refund_amount": payment.amount if payment else None,
        "refund_reference": payment.gateway_refund_id if payment else "",
    }
    template = f"notifications/email/{log.kind}"
    subject = SUBJECTS[log.kind].format(order=order.order_number, invoice=invoice["invoice_number"])
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(f"{template}.txt", context),
        to=[log.recipient],
        reply_to=[settings.SELLER["email"]] if settings.SELLER.get("email") else None,
    )
    message.attach_alternative(render_to_string(f"{template}.html", context), "text/html")
    if log.kind == Kind.INVOICE:
        _attach_invoice_pdf(message, invoice)
    return message


def _attach_invoice_pdf(message: EmailMultiAlternatives, invoice: dict) -> None:
    """Attach the invoice as a PDF. The email body already holds the whole invoice, so if the PDF cannot be
    built the email still goes out without it (and the problem is logged) instead of failing every retry."""
    from apps.orders.invoice_pdf import render_invoice_pdf

    try:
        pdf = render_invoice_pdf(invoice)
    except Exception:
        logger.exception("Could not build the PDF for invoice %s", invoice["invoice_number"])
        return
    message.attach(f"Invoice-{invoice['invoice_number']}.pdf", pdf, "application/pdf")
