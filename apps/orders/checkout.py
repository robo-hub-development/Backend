"""Checkout orchestration: ties the cart, orders and payments together.

apps.orders.services holds the low-level order operations and apps.payments.services the payment
lifecycle; this module is the only place that coordinates both for a customer-facing action.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.notifications import services as notifications
from apps.payments import services as payments
from apps.payments.models import Payment

from . import services
from .models import Order, OrderCharge
from .pricing import quote_cod_fee
from .services import ChargeSpec, Purchase


@dataclass(frozen=True)
class Pricing:
    subtotal: Decimal
    charges: list[ChargeSpec]
    total: Decimal
    payable_now: Decimal  # online money due before the order moves on (full price, or the COD fee)
    due_on_delivery: Decimal  # cash for the courier (COD only)


@dataclass(frozen=True)
class PlacedOrder:
    order: Order
    payment: Payment | None  # the online payment the shopper must complete, if any


def price(subtotal: Decimal, payment_method: str, *, state: str, pincode: str) -> Pricing:
    """What an order costs and how it is paid. COD: the items are paid in cash, the transit fee online."""
    if payment_method == Order.PaymentMethod.COD:
        fee, rule = quote_cod_fee(subtotal, state=state, pincode=pincode)
        charges = (
            [ChargeSpec(OrderCharge.Kind.COD_TRANSIT, "Cash on delivery transit fee", fee, rule)]
            if fee
            else []
        )
        return Pricing(subtotal, charges, subtotal + fee, payable_now=fee, due_on_delivery=subtotal)
    return Pricing(subtotal, [], subtotal, payable_now=subtotal, due_on_delivery=Decimal(0))


def _check_cod_rule(lines) -> None:
    # PRD 1.4: cash on delivery is for one piece only.
    if not (len(lines) == 1 and lines[0].quantity == 1):
        raise services.fail("Cash on delivery is available only for a single item.")


def quote(user, cart_id, cart_item_ids, *, payment_method: str, state: str, pincode: str) -> Pricing:
    """Price an order without creating or holding anything (for the checkout summary)."""
    _, lines = services.lock_cart_lines(user, cart_id, cart_item_ids, lock=False)
    if payment_method == Order.PaymentMethod.COD:
        _check_cod_rule(lines)
    subtotal = sum((line.product.price * line.quantity for line in lines), Decimal(0))
    return price(subtotal, payment_method, state=state, pincode=pincode)


def place_order(
    user, cart_id, cart_item_ids, address: dict, payment_method: str, flow: str = "link"
) -> PlacedOrder:
    """Create the order now, hold its stock, and open the online payment it needs (if any).

    - online: the whole price is due online; the order waits in AWAITING_PAYMENT.
    - cod: the item price is paid in cash on delivery and the transit fee online up front. With no fee the
      order is PENDING at once; with a fee it waits in AWAITING_PAYMENT like an online order.
    Quantities and prices always come from the cart and the catalogue, never from the client.
    """
    with transaction.atomic():
        cart, lines = services.lock_cart_lines(user, cart_id, cart_item_ids)
        if payment_method == Order.PaymentMethod.COD:
            _check_cod_rule(lines)
        products = services.take_stock([(line.product_id, line.quantity) for line in lines])
        purchases = [
            Purchase(products[line.product_id], line.quantity, products[line.product_id].price, line)
            for line in lines
        ]
        pricing = price(
            sum((p.unit_price * p.quantity for p in purchases), Decimal(0)),
            payment_method,
            state=address["state"],
            pincode=address["pincode"],
        )
        if pricing.payable_now > 0:
            payments.ensure_gateway_configured()  # rolls back the stock if online payments cannot work

        awaiting = pricing.payable_now > 0
        order = services.create_order(
            user=user,
            cart=cart,
            purchases=purchases,
            charges=pricing.charges,
            address=address,
            payment_method=payment_method,
            status=Order.Status.AWAITING_PAYMENT if awaiting else Order.Status.PENDING,
            payment_due_by=timezone.now() + timedelta(minutes=settings.ORDER_PAYMENT_DUE_MINUTES)
            if awaiting
            else None,
        )
        online = None
        if awaiting:
            purpose = (
                Payment.Purpose.FULL
                if payment_method == Order.PaymentMethod.ONLINE
                else Payment.Purpose.COD_FEE
            )
            online = payments.create_online_payment(
                order, purpose=purpose, amount=pricing.payable_now, flow=flow
            )
        if payment_method == Order.PaymentMethod.COD:
            payments.create_cash_payment(order, amount=pricing.due_on_delivery)
        if not awaiting:
            services.commit_cart_lines(order)
        payments.sync_summary(order)
        if not awaiting:
            notifications.after_commit(notifications.notify_order_confirmed, order.pk)

    if online is not None:
        try:
            payments.start_gateway_payment(online)
        except payments.PaymentStartFailed:
            _abandon(order.pk, user, "payment could not be started")
            raise
    return PlacedOrder(order, online)


def _abandon(order_id, user, reason: str) -> None:
    """Cancel an order whose payment never got off the ground, so it does not sit holding stock."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == Order.Status.AWAITING_PAYMENT:
            payments.release_unpaid(order, Order.Status.CANCELLED, actor=user, reason=reason)


def pay_order(user, order_id, flow: str = "link") -> tuple[Order, Payment]:
    """(Re)start the online payment of an order that is still waiting for money."""
    payments.ensure_gateway_configured()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(user=user, pk=order_id).first()
        if order is None:
            raise NotFound
        if order.status != Order.Status.AWAITING_PAYMENT:
            raise services.fail("This order is not waiting for payment.")
        payment = payments.retry_payment(order, flow)
    if not (payment.gateway_link_id or payment.gateway_order_id):
        payments.start_gateway_payment(payment)  # a fresh payment; an existing one is simply handed back
    return order, payment


def _cancel_locked(order: Order, *, actor, reason: str) -> bool:
    """Cancel a locked order and put everything back: stock, the unpaid payments, the cash balance.

    Returns whether paid online money must now be refunded (the caller does that after the commit)."""
    if order.status == Order.Status.AWAITING_PAYMENT:
        payments.release_unpaid(order, Order.Status.CANCELLED, actor=actor, reason=reason)
        return False
    services.return_order_stock(order)
    payments.close_cash(order, Payment.Status.CANCELLED)
    services.transition(order, Order.Status.CANCELLED, actor=actor, reason=reason)
    payments.sync_summary(order)
    return True


def cancel_order(user, order_id) -> Order:
    """Cancel the user's own order if the status map and the cancellation window allow it; restore stock.

    Unpaid: nothing to refund. Paid online (in full, or the COD fee) and not yet dispatched: refunded after
    the cancellation is committed. A cash balance that was never collected is simply dropped.
    """
    with transaction.atomic():
        # Lock the order so two concurrent cancels cannot both restore stock. Foreign ids behave as unknown.
        order = Order.objects.select_for_update().filter(user=user, pk=order_id).first()
        if order is None:
            raise NotFound
        if not order.can_cancel:
            raise services.fail("This order can no longer be cancelled.")
        refund = _cancel_locked(order, actor=user, reason="cancelled by customer")
    if refund:
        payments.refund_order(order, actor=user, reason="cancelled before dispatch")
        order.refresh_from_db()
    return order


# Fulfilment steps. Staff-facing (the admin phase wires them to actions); they live here so the money
# rules are enforced in one place whoever triggers them.


def mark_delivered(order_id, *, actor=None) -> Order:
    """Delivered: for COD the courier's cash is now collected, so nothing is left to pay."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        services.transition(order, Order.Status.DELIVERED, actor=actor, reason="delivered")
        payments.collect_cash(order, actor=actor)
    return order


def mark_return_to_origin(order_id, *, actor=None) -> Order:
    """The customer refused delivery. The online COD fee is kept for the transit; the cash is never due."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        services.transition(order, Order.Status.RTO, actor=actor, reason="customer refused delivery")
        payments.close_cash(order, Payment.Status.UNCOLLECTABLE)
        payments.sync_summary(order)
    return order


def confirm_order(order_id, *, actor=None) -> Order:
    """Staff accept an order that is waiting for them (paid what was due online, COD cash due on delivery)."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PENDING:
            raise services.InvalidTransition(f"{order.order_number}: only a pending order can be accepted")
        services.transition(order, Order.Status.CONFIRMED, actor=actor, reason="accepted by staff")
    return order


def mark_shipped(order_id, *, actor=None, courier_name: str = "", tracking_number: str = "") -> Order:
    """Handed to the delivery partner. The tracking details are saved first so the invoice email that goes out
    with the status change already carries them."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        fields = []
        for name, value in (("courier_name", courier_name), ("tracking_number", tracking_number)):
            value = value.strip()
            if value:
                setattr(order, name, value)
                fields.append(name)
        if fields:
            order.save(update_fields=[*fields, "updated_at"])
        services.transition(order, Order.Status.SHIPPED, actor=actor, reason="handed to delivery partner")
    return order


def cancel_order_by_staff(order_id, *, actor, note: str = "") -> Order:
    """Staff cancel an order the customer can no longer cancel (or that never will): no cancellation window,
    same stock, cash and refund handling. Only orders not yet shipped can be cancelled."""
    reason = f"cancelled by staff: {note.strip()}" if note.strip() else "cancelled by staff"
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if not order.can_transition_to(Order.Status.CANCELLED):
            raise services.InvalidTransition(
                f"{order.order_number}: a {order.get_status_display().lower()} order cannot be cancelled"
            )
        refund = _cancel_locked(order, actor=actor, reason=reason[:200])
    if refund:
        payments.refund_order(order, actor=actor, reason="cancelled by staff before dispatch")
        order.refresh_from_db()
    return order
