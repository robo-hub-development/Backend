import json
from decimal import Decimal

import pytest

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders import checkout
from apps.orders.models import Order, OrderItem
from apps.payments import services
from apps.payments.models import Payment, PaymentTransaction, WebhookEvent

from .conftest import client_for
from .helpers import (
    checkout_payload,
    fill_cart,
    link_payload,
    live_cart_lines,
    online_order,
    online_payment,
    pay,
    place,
    send_webhook,
    sign,
    stock_of,
)


@pytest.mark.django_db
class TestSignature:
    def test_missing_or_wrong_signature_is_rejected_and_changes_nothing(self, api):
        _, order, payment = online_order(api)
        payload = link_payload("payment_link.paid", payment)
        body = json.dumps(payload).encode()
        for bad in ("", "deadbeef", sign(body, "other-secret")):
            assert send_webhook(payload, signature=bad).status_code == 400
        order.refresh_from_db()
        assert order.status == "awaiting_payment" and not WebhookEvent.objects.exists()
        assert not PaymentTransaction.objects.exists()

    def test_signature_covers_the_exact_body(self, api):
        _, _, payment = online_order(api)
        body = json.dumps(link_payload("payment_link.paid", payment)).encode()
        assert (
            send_webhook(None, raw=body.replace(b"pay_1", b"pay_2"), signature=sign(body)).status_code == 400
        )

    def test_no_webhook_secret_configured_rejects_everything(self, api, settings):
        _, _, payment = online_order(api)
        settings.RAZORPAY_WEBHOOK_SECRET = ""
        assert send_webhook(link_payload("payment_link.paid", payment), signature="x").status_code == 400

    def test_malformed_bodies(self):
        assert send_webhook(None, raw=b"not json").status_code == 400
        assert send_webhook(None, raw=b"[1, 2]").status_code == 400
        raw = b'{"event": "payment_link.paid", "payload": {"payment_link": 5}}'
        assert send_webhook(None, raw=raw).status_code == 400


@pytest.mark.django_db
class TestPaidSettlesTheOrder:
    def test_paid_confirms_the_same_order_and_records_the_charge(self, api, member):
        a, b = make_product(price=100, stock_quantity=9), make_product(price=40, stock_quantity=9)
        cart_id = fill_cart(api, (a, 3), (b, 2))
        assert place(api, cart_id).status_code == 201
        order = Order.objects.get()
        payment = online_payment(order)
        order_id = order.pk

        res = pay(payment, payment_id="pay_abc")

        assert res.status_code == 200 and res.data["result"] == "processed"
        order.refresh_from_db()
        assert order.pk == order_id  # the order created at checkout is the one that is confirmed
        assert (order.status, order.payment_method, order.payment_status) == ("confirmed", "online", "paid")
        assert (order.amount_paid, order.total, order.amount_due) == (380, 380, 0)
        assert OrderItem.objects.filter(order=order, cart_item__isnull=False).count() == 2
        assert live_cart_lines() == 0 and CartItem.objects.filter(ordered_at__isnull=False).count() == 2
        payment.refresh_from_db()
        assert (payment.status, payment.gateway_payment_id) == ("captured", "pay_abc")
        charge = payment.transactions.get()
        assert (charge.kind, charge.amount, charge.gateway_ref) == ("charge", 380, "pay_abc")
        assert (stock_of(a), stock_of(b)) == (6, 7)  # taken once, at checkout
        assert [e.to_status for e in order.status_events.all()] == ["awaiting_payment", "confirmed"]
        assert api.get("/api/v1/cart").data["items"] == []

    def test_price_is_fixed_when_the_order_is_placed(self, api):
        product, order, payment = online_order(api, price=250)
        product.price = 999
        product.save()
        pay(payment)
        order.refresh_from_db()
        assert order.total == 250 and order.items.get().unit_price == 250

    def test_redelivered_event_is_a_no_op(self, api):
        _, order, payment = online_order(api)
        assert pay(payment, event_id="evt_x").data["result"] == "processed"
        assert pay(payment, event_id="evt_x").data["result"] == "duplicate"
        assert PaymentTransaction.objects.count() == 1 and WebhookEvent.objects.count() == 1

    def test_the_same_payment_under_a_new_event_id_is_still_counted_once(self, api):
        product, order, payment = online_order(api, stock=5)
        pay(payment, event_id="evt_1")
        pay(payment, event_id="evt_2")
        assert PaymentTransaction.objects.count() == 1
        order.refresh_from_db()
        assert order.amount_paid == order.total and stock_of(product) == 4

    def test_wrong_amount_is_not_accepted_and_flagged_for_refund(self, api):
        _, order, payment = online_order(api, price=250)
        assert pay(payment, amount_paise=100).status_code == 200
        payment.refresh_from_db()
        order.refresh_from_db()
        assert (payment.status, payment.failure_reason) == ("needs_refund", "amount_mismatch")
        assert order.status == "awaiting_payment" and order.amount_paid == 0
        assert not PaymentTransaction.objects.exists()

    def test_a_failure_while_applying_is_not_recorded_so_the_retry_works(self, api, monkeypatch):
        _, order, payment = online_order(api)
        monkeypatch.setattr(services, "settle_captured", lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
        assert pay(payment, event_id="evt_r").status_code == 500
        assert not WebhookEvent.objects.exists()
        monkeypatch.undo()
        assert pay(payment, event_id="evt_r").data["result"] == "processed"
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_unknown_references_and_other_links_are_ignored(self, api):
        _, order, payment = online_order(api)
        ghost = Payment(reference="0" * 32, amount=Decimal(1))
        assert (
            send_webhook(link_payload("payment_link.paid", ghost, link_id="plink_x")).data["result"]
            == "unknown"
        )
        mismatched = link_payload("payment_link.paid", payment, link_id="plink_other")
        assert send_webhook(mismatched, event_id="evt_m").data["result"] == "unknown"
        order.refresh_from_db()
        assert order.status == "awaiting_payment"


@pytest.mark.django_db
class TestPaymentLifecycleEvents:
    @pytest.mark.parametrize(
        ("event", "status", "reason"),
        [("payment_link.cancelled", "failed", "cancelled"), ("payment_link.expired", "expired", "expired")],
    )
    def test_a_closed_link_fails_the_payment_but_keeps_the_order_for_a_retry(
        self, api, event, status, reason
    ):
        product, order, payment = online_order(api, stock=5)
        assert send_webhook(link_payload(event, payment), event_id="evt_n").status_code == 200
        payment.refresh_from_db()
        order.refresh_from_db()
        assert (payment.status, payment.failure_reason) == (status, reason)
        assert order.status == "awaiting_payment" and stock_of(product) == 4  # still held for the retry
        assert api.post(f"/api/v1/orders/{order.pk}/pay", {}, format="json").status_code == 200

    def test_payment_failed_attempts_are_ignored(self, api):
        _, _, payment = online_order(api)
        assert send_webhook(link_payload("payment.failed", payment)).data["result"] == "ignored"
        payment.refresh_from_db()
        assert payment.status == "pending"

    def test_paying_an_older_failed_link_still_settles_the_order(self, api):
        _, order, first = online_order(api)
        send_webhook(link_payload("payment_link.expired", first), event_id="evt_e")
        api.post(f"/api/v1/orders/{order.pk}/pay", {}, format="json")
        second = online_payment(order)
        assert second.pk != first.pk

        pay(first, event_id="evt_late_first")  # the customer paid the first link after all

        order.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        assert order.status == "confirmed" and first.status == "captured"
        assert (second.status, second.failure_reason) == ("failed", "superseded")  # cannot be paid twice

    def test_a_second_payment_for_an_already_paid_order_needs_a_refund(self, api):
        _, order, first = online_order(api)
        send_webhook(link_payload("payment_link.expired", first), event_id="evt_e")
        api.post(f"/api/v1/orders/{order.pk}/pay", {}, format="json")
        second = online_payment(order)
        pay(second, event_id="evt_2", payment_id="pay_2")
        pay(first, event_id="evt_1", payment_id="pay_1")  # the old link was paid as well

        first.refresh_from_db()
        assert (first.status, first.failure_reason) == ("needs_refund", "order_confirmed")
        order.refresh_from_db()
        assert order.amount_paid == order.total  # only one payment counts towards the order


@pytest.mark.django_db
class TestLatePayments:
    def expire(self, order):
        from apps.payments import services as svc

        Order.objects.filter(pk=order.pk).update(payment_due_by="2020-01-01T00:00:00Z")
        assert svc.expire_unpaid_orders() == 1
        order.refresh_from_db()
        assert order.status == "expired"

    def test_paid_after_expiry_takes_stock_again_and_revives_the_order(self, api):
        product, order, payment = online_order(api, stock=3)
        self.expire(order)
        assert stock_of(product) == 3
        pay(payment, event_id="evt_late")
        order.refresh_from_db()
        assert order.status == "confirmed" and order.payment_status == "paid"
        assert stock_of(product) == 2
        assert [e.to_status for e in order.status_events.all()] == [
            "awaiting_payment",
            "expired",
            "confirmed",
        ]

    def test_paid_after_expiry_with_no_stock_left_needs_a_refund(self, api, other_member):
        product, order, payment = online_order(api, stock=1)
        self.expire(order)
        buyer = client_for(other_member)
        assert (
            place(buyer, fill_cart(buyer, (product, 1))).status_code == 201
        )  # someone else took the last one

        pay(payment, event_id="evt_late")

        payment.refresh_from_db()
        order.refresh_from_db()
        assert (payment.status, payment.failure_reason) == ("needs_refund", "out_of_stock")
        assert order.status == "expired" and order.amount_paid == 0
        assert stock_of(product) == 0

    def test_paid_after_expiry_when_the_cart_line_was_ordered_elsewhere_needs_a_refund(self, api):
        product, order, payment = online_order(api, stock=5)
        self.expire(order)
        CartItem.objects.update(ordered_at="2026-01-01T00:00:00Z")
        pay(payment, event_id="evt_late")
        payment.refresh_from_db()
        assert (payment.status, payment.failure_reason) == ("needs_refund", "cart_changed")
        assert stock_of(product) == 5  # the stock taken for the attempt was given back

    def test_paid_after_the_customer_cancelled_needs_a_refund(self, api):
        product, order, payment = online_order(api, stock=5)
        assert api.post(f"/api/v1/orders/{order.pk}/cancel").status_code == 200
        pay(payment)
        payment.refresh_from_db()
        assert (payment.status, payment.failure_reason) == ("needs_refund", "order_cancelled")
        assert stock_of(product) == 5


@pytest.mark.django_db
class TestCheckoutEvents:
    def started(self, api, **kw):
        return online_order(api, flow="checkout", **kw)

    @pytest.mark.parametrize("event", ["payment.captured", "order.paid"])
    def test_captured_confirms_the_order(self, api, event):
        product, order, payment = self.started(api, qty=1)
        res = send_webhook(checkout_payload(event, payment, payment_id="pay_77"), event_id="evt_1")
        assert res.data["result"] == "processed"
        order.refresh_from_db()
        payment.refresh_from_db()
        assert (order.status, order.payment_status) == ("confirmed", "paid")
        assert (payment.status, payment.gateway_payment_id) == ("captured", "pay_77")
        assert stock_of(product) == 4

    def test_both_events_and_redeliveries_count_once(self, api):
        _, order, payment = self.started(api)
        send_webhook(checkout_payload("payment.captured", payment), event_id="evt_a")
        send_webhook(checkout_payload("order.paid", payment), event_id="evt_b")
        dup = send_webhook(checkout_payload("payment.captured", payment), event_id="evt_a")
        assert dup.data["result"] == "duplicate" and PaymentTransaction.objects.count() == 1

    def test_amount_mismatch_needs_a_refund(self, api):
        _, order, payment = self.started(api, price=250)
        send_webhook(checkout_payload("payment.captured", payment, amount_paise=100))
        payment.refresh_from_db()
        assert (payment.status, payment.failure_reason) == ("needs_refund", "amount_mismatch")

    def test_unknown_razorpay_order_and_failed_tries_are_ignored(self, api):
        _, _, payment = self.started(api)
        unknown = send_webhook(checkout_payload("payment.captured", payment, order_id="order_not_ours"))
        assert unknown.data["result"] == "unknown"
        assert send_webhook(checkout_payload("payment.failed", payment)).data["result"] == "ignored"
        payment.refresh_from_db()
        assert payment.status == "pending"

    def test_authorized_is_captured_for_the_expected_amount_and_the_capture_event_settles_it(
        self, api, gateway
    ):
        _, order, payment = self.started(api, price=250)
        auth = send_webhook(
            checkout_payload("payment.authorized", payment, payment_id="pay_a"), event_id="e1"
        )
        assert auth.data["result"] == "processed"
        assert gateway.captures == [{"payment_id": "pay_a", "amount_paise": 25000}]
        order.refresh_from_db()
        assert order.status == "awaiting_payment"  # settled by payment.captured, not by the authorization
        send_webhook(checkout_payload("payment.captured", payment, payment_id="pay_a"), event_id="e2")
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_redelivered_authorization_captures_once(self, api, gateway):
        _, _, payment = self.started(api)
        for _ in range(2):
            send_webhook(checkout_payload("payment.authorized", payment), event_id="e1")
        assert len(gateway.captures) == 1

    def test_wrong_amount_or_settled_payment_is_never_captured(self, api, gateway):
        _, _, payment = self.started(api, price=250)
        assert (
            send_webhook(checkout_payload("payment.authorized", payment, amount_paise=100)).data["result"]
            == "ignored"
        )
        send_webhook(checkout_payload("payment.captured", payment), event_id="evt_cap")
        send_webhook(checkout_payload("payment.authorized", payment), event_id="evt_auth")
        assert gateway.captures == []

    def test_a_failing_capture_is_retried_by_the_gateway(self, api, gateway):
        _, _, payment = self.started(api)
        gateway.fail_capture = True
        assert send_webhook(checkout_payload("payment.authorized", payment), event_id="e1").status_code == 500
        assert not WebhookEvent.objects.exists()
        gateway.fail_capture = False
        assert (
            send_webhook(checkout_payload("payment.authorized", payment), event_id="e1").data["result"]
            == "processed"
        )
        assert len(gateway.captures) == 1


@pytest.mark.django_db
def test_checkout_hooks_cannot_dispatch_an_order_that_is_not_paid(api):
    """Fulfilment steps follow the transition map: an unpaid order cannot be marked delivered."""
    from apps.orders.services import InvalidTransition

    _, order, _ = online_order(api)
    with pytest.raises(InvalidTransition):
        checkout.mark_delivered(order.pk)
    order.refresh_from_db()
    assert (
        order.status == "awaiting_payment" and not order.status_events.filter(to_status="delivered").exists()
    )
    assert live_cart_lines() == 1
