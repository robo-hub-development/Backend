import json
from decimal import Decimal

import pytest

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order, OrderItem
from apps.payments import services
from apps.payments.models import PaymentAttempt, WebhookEvent

from .helpers import event_payload, fill_cart, pay, send_webhook, sign, start, start_for


def started(api, *, stock=5, qty=1, price=250):
    product = make_product(price=price, stock_quantity=stock)
    res, attempt = start_for(api, (product, qty))
    assert res.status_code == 200
    return product, attempt


@pytest.mark.django_db
class TestSignature:
    def test_missing_or_wrong_signature_is_rejected_and_changes_nothing(self, api):
        product, attempt = started(api)
        payload = event_payload("payment_link.paid", attempt)
        assert send_webhook(payload, signature="").status_code == 400
        assert send_webhook(payload, signature="deadbeef").status_code == 400
        body = json.dumps(payload).encode()
        assert send_webhook(payload, signature=sign(body, "other-secret")).status_code == 400
        attempt.refresh_from_db()
        assert attempt.status == "pending"
        assert not Order.objects.exists()
        assert not WebhookEvent.objects.exists()

    def test_signature_covers_the_exact_body(self, api):
        _, attempt = started(api)
        body = json.dumps(event_payload("payment_link.paid", attempt)).encode()
        signature = sign(body)
        tampered = body.replace(b"pay_1", b"pay_2")
        assert send_webhook(None, raw=tampered, signature=signature).status_code == 400

    def test_no_webhook_secret_configured_rejects_everything(self, api, settings):
        _, attempt = started(api)
        settings.RAZORPAY_WEBHOOK_SECRET = ""
        assert send_webhook(event_payload("payment_link.paid", attempt), signature="x").status_code == 400

    def test_malformed_bodies(self, api):
        assert send_webhook(None, raw=b"not json").status_code == 400
        assert send_webhook(None, raw=b"[1, 2]").status_code == 400
        assert (
            send_webhook(
                None, raw=b'{"event": "payment_link.paid", "payload": {"payment_link": 5}}'
            ).status_code
            == 400
        )


@pytest.mark.django_db
class TestPaid:
    def test_paid_creates_exactly_one_confirmed_order_mapped_to_the_cart_lines(self, api, member):
        a, b = make_product(price=100, stock_quantity=9), make_product(price=40, stock_quantity=9)
        res, attempt = start_for(api, (a, 3), (b, 2))
        lines = {line.product_id: line for line in CartItem.objects.all()}

        res = pay(attempt, payment_id="pay_abc")

        assert res.status_code == 200 and res.data["result"] == "processed"
        order = Order.objects.get()
        assert (order.status, order.payment_method, order.payment_status) == ("confirmed", "online", "paid")
        assert order.user == member and order.total == 380
        assert order.ship_city == "Bengaluru" and order.ship_pincode == "560001"
        assert {i.cart_item_id for i in order.items.all()} == {line.pk for line in lines.values()}
        assert sorted(i.quantity for i in order.items.all()) == [2, 3]
        attempt.refresh_from_db()
        assert (attempt.status, attempt.order, attempt.gateway_payment_id) == ("paid", order, "pay_abc")
        assert all(line.ordered_at for line in CartItem.objects.all())
        a.refresh_from_db()
        b.refresh_from_db()
        assert (a.stock_quantity, b.stock_quantity) == (6, 7)  # stock was taken once, when payment started
        assert api.get("/api/v1/cart").data["items"] == []
        assert [o["orderNumber"] for o in api.get("/api/v1/orders").data["items"]] == [order.order_number]

    def test_order_uses_the_price_from_when_payment_started(self, api):
        product, attempt = started(api, price=250)
        product.price = 999
        product.save()
        pay(attempt)
        order = Order.objects.get()
        assert order.total == 250
        assert OrderItem.objects.get().unit_price == 250

    def test_redelivered_event_is_a_no_op(self, api):
        product, attempt = started(api)
        assert pay(attempt, event_id="evt_x").data["result"] == "processed"
        assert pay(attempt, event_id="evt_x").data["result"] == "duplicate"
        assert Order.objects.count() == 1
        assert WebhookEvent.objects.count() == 1

    def test_same_payment_under_a_new_event_id_still_makes_one_order(self, api):
        product, attempt = started(api, stock=5)
        pay(attempt, event_id="evt_1")
        pay(attempt, event_id="evt_2")
        assert Order.objects.count() == 1
        product.refresh_from_db()
        assert product.stock_quantity == 4

    def test_amount_mismatch_creates_no_order_and_flags_for_refund(self, api):
        product, attempt = started(api, price=250)
        res = pay(attempt, amount_paise=100)
        assert res.status_code == 200
        attempt.refresh_from_db()
        assert (attempt.status, attempt.failure_reason) == ("needs_refund", "amount_mismatch")
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 5  # nothing will ship, so the stock goes back

    def test_failure_while_applying_is_not_recorded_so_the_retry_works(self, api, monkeypatch):
        _, attempt = started(api)
        monkeypatch.setattr(services, "finalise_paid", lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
        assert pay(attempt, event_id="evt_r").status_code == 500
        assert not WebhookEvent.objects.exists()
        monkeypatch.undo()
        assert pay(attempt, event_id="evt_r").data["result"] == "processed"
        assert Order.objects.count() == 1


@pytest.mark.django_db
class TestNotPaid:
    @pytest.mark.parametrize(
        ("event", "reason"), [("payment_link.cancelled", "cancelled"), ("payment_link.expired", "expired")]
    )
    def test_cancelled_or_expired_releases_stock_and_keeps_the_cart(self, api, event, reason):
        product, attempt = started(api, stock=5, qty=2)
        res = send_webhook(event_payload(event, attempt), event_id="evt_n")
        assert res.status_code == 200
        attempt.refresh_from_db()
        assert (attempt.status, attempt.failure_reason) == ("failed", reason)
        product.refresh_from_db()
        assert product.stock_quantity == 5
        assert not Order.objects.exists()
        assert CartItem.objects.get().ordered_at is None
        assert start(api, attempt.cart_id).status_code == 200  # the shopper can try again

    def test_payment_failed_attempts_are_ignored(self, api):
        _, attempt = started(api)
        res = send_webhook(event_payload("payment.failed", attempt))
        assert res.data["result"] == "ignored"
        attempt.refresh_from_db()
        assert attempt.status == "pending"

    def test_unknown_reference_and_mismatched_link_are_ignored(self, api):
        _, attempt = started(api)
        attempt_like = PaymentAttempt(reference="0" * 32, amount=Decimal(1))
        assert (
            send_webhook(event_payload("payment_link.paid", attempt_like, link_id="plink_x")).data["result"]
            == "unknown"
        )
        res = send_webhook(
            event_payload("payment_link.paid", attempt, link_id="plink_other"), event_id="evt_m"
        )
        assert res.data["result"] == "unknown"
        assert not Order.objects.exists()


@pytest.mark.django_db
class TestLatePayment:
    def test_paid_after_expiry_takes_stock_again_and_creates_the_order(self, api):
        product, attempt = started(api, stock=3)
        send_webhook(event_payload("payment_link.expired", attempt), event_id="evt_e")
        product.refresh_from_db()
        assert product.stock_quantity == 3

        pay(attempt, event_id="evt_late")

        attempt.refresh_from_db()
        assert attempt.status == "paid" and Order.objects.count() == 1
        product.refresh_from_db()
        assert product.stock_quantity == 2

    def test_paid_after_expiry_with_no_stock_left_needs_a_refund(self, api, other_member):
        from .conftest import client_for

        product, attempt = started(api, stock=1)
        send_webhook(event_payload("payment_link.expired", attempt), event_id="evt_e")
        buyer = client_for(other_member)
        assert start(buyer, fill_cart(buyer, (product, 1))).status_code == 200  # someone else took it

        pay(attempt, event_id="evt_late")

        attempt.refresh_from_db()
        assert (attempt.status, attempt.failure_reason) == ("needs_refund", "out_of_stock")
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 0

    def test_paid_after_expiry_when_the_cart_line_was_ordered_elsewhere_needs_a_refund(self, api):
        product, attempt = started(api, stock=5)
        send_webhook(event_payload("payment_link.expired", attempt), event_id="evt_e")
        CartItem.objects.update(ordered_at="2026-01-01T00:00:00Z")
        pay(attempt, event_id="evt_late")
        attempt.refresh_from_db()
        assert (attempt.status, attempt.failure_reason) == ("needs_refund", "cart_changed")
        assert not Order.objects.exists()
