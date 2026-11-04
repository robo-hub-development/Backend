from datetime import timedelta
from unittest import mock

import pytest
import requests
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order
from apps.payments import services
from apps.payments.gateway import (
    GatewayError,
    LinkStatus,
    PaymentsNotConfigured,
    RazorpayGateway,
    get_gateway,
    verify_webhook_signature,
)
from apps.payments.models import PaymentAttempt

from .conftest import client_for
from .helpers import event_payload, pay, send_webhook, sign, start_for


def started(api, *, stock=5, price=250):
    product = make_product(price=price, stock_quantity=stock)
    res, attempt = start_for(api, (product, 1))
    assert res.status_code == 200
    return product, attempt


def status_url(attempt):
    return f"/api/v1/payments/{attempt.reference}"


@pytest.mark.django_db
class TestPaymentStatus:
    def test_pending_while_nothing_has_happened(self, api):
        _, attempt = started(api)
        res = api.get(status_url(attempt))
        assert res.status_code == 200
        assert res.data == {"status": "pending"}

    def test_success_with_the_order_after_the_webhook(self, api):
        _, attempt = started(api)
        pay(attempt)
        res = api.get(status_url(attempt))
        assert res.data["status"] == "success"
        assert res.data["order"]["status"] == "confirmed"
        assert res.data["order"]["paymentStatus"] == "paid"
        assert res.data["order"]["paymentMethod"] == "online"
        assert res.data["order"]["total"] == 250

    def test_a_late_webhook_is_covered_by_asking_the_gateway(self, api, gateway):
        _, attempt = started(api)
        gateway.links[attempt.gateway_link_id] = LinkStatus(
            "paid", payment_id="pay_9", amount_paid_paise=25000
        )
        first = api.get(status_url(attempt))
        second = api.get(status_url(attempt))
        assert first.data["status"] == second.data["status"] == "success"
        assert first.data["order"]["id"] == second.data["order"]["id"]
        assert Order.objects.count() == 1
        attempt.refresh_from_db()
        assert attempt.gateway_payment_id == "pay_9"

    def test_gateway_reports_cancelled_or_expired(self, api, gateway):
        product, attempt = started(api)
        gateway.links[attempt.gateway_link_id] = LinkStatus("expired")
        assert api.get(status_url(attempt)).data == {"status": "failed"}
        product.refresh_from_db()
        assert product.stock_quantity == 5

    def test_gateway_down_still_answers_pending(self, api, gateway):
        _, attempt = started(api)
        gateway.fail_fetch = True
        res = api.get(status_url(attempt))
        assert res.status_code == 200 and res.data == {"status": "pending"}

    def test_paid_but_unfulfillable_reads_as_failed(self, api):
        _, attempt = started(api)
        pay(attempt, amount_paise=1)
        assert api.get(status_url(attempt)).data == {"status": "failed"}

    def test_only_the_owner_can_see_a_payment(self, api, other_member):
        _, attempt = started(api)
        assert client_for(other_member).get(status_url(attempt)).status_code == 404
        assert api.get("/api/v1/payments/" + "0" * 32).status_code == 404
        assert APIClient().get(status_url(attempt)).status_code == 401


def paid_online_order(api, gateway, *, stock=5, price=250):
    product, attempt = started(api, stock=stock, price=price)
    pay(attempt, payment_id="pay_77")
    return product, attempt, Order.objects.get()


@pytest.mark.django_db
class TestRefundOnCancel:
    def test_cancelling_a_paid_online_order_refunds_it_and_restores_stock(self, api, gateway):
        product, attempt, order = paid_online_order(api, gateway)
        res = api.post(f"/api/v1/orders/{order.pk}/cancel")
        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"]) == ("cancelled", "refunded")
        assert gateway.refunds == [{"payment_id": "pay_77", "amount_paise": 25000}]
        attempt.refresh_from_db()
        assert attempt.refund_id == "rfnd_1" and attempt.refunded_at is not None
        product.refresh_from_db()
        assert product.stock_quantity == 5

    def test_a_failed_refund_does_not_undo_the_cancellation(self, api, gateway):
        gateway.fail_refund = True
        product, attempt, order = paid_online_order(api, gateway)
        res = api.post(f"/api/v1/orders/{order.pk}/cancel")
        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"]) == (
            "cancelled",
            "paid",
        )  # staff refund by hand
        attempt.refresh_from_db()
        assert attempt.refund_id == ""
        product.refresh_from_db()
        assert product.stock_quantity == 5

    def test_cancelling_twice_refunds_once(self, api, gateway):
        _, _, order = paid_online_order(api, gateway)
        api.post(f"/api/v1/orders/{order.pk}/cancel")
        assert api.post(f"/api/v1/orders/{order.pk}/cancel").status_code == 400
        assert len(gateway.refunds) == 1

    def test_cod_cancellation_never_touches_the_gateway(self, api, gateway):
        from apps.orders.tests.test_orders_api import place

        order_id = place(api, (make_product(), 1)).data["order"]["id"]
        assert api.post(f"/api/v1/orders/{order_id}/cancel").status_code == 200
        assert gateway.refunds == []

    def test_shipped_online_orders_cannot_be_cancelled_or_refunded(self, api, gateway):
        _, _, order = paid_online_order(api, gateway)
        Order.objects.update(status="shipped")
        assert api.post(f"/api/v1/orders/{order.pk}/cancel").status_code == 400
        assert gateway.refunds == []


@pytest.mark.django_db
class TestExpireCommand:
    def test_stale_attempts_release_their_stock_and_fresh_ones_are_left(self, api, gateway, other_member):
        stale_product, stale = started(api, stock=5)
        fresh_product, fresh = started(client_for(other_member), stock=5)
        PaymentAttempt.objects.filter(pk=stale.pk).update(expires_at=timezone.now() - timedelta(minutes=30))

        call_command("expire_payments")

        stale.refresh_from_db()
        fresh.refresh_from_db()
        assert (stale.status, stale.failure_reason) == ("failed", "expired")
        assert fresh.status == "pending"
        stale_product.refresh_from_db()
        fresh_product.refresh_from_db()
        assert (stale_product.stock_quantity, fresh_product.stock_quantity) == (5, 4)

    def test_an_attempt_that_was_actually_paid_becomes_an_order_not_an_expiry(self, api, gateway):
        product, attempt = started(api, stock=5)
        gateway.links[attempt.gateway_link_id] = LinkStatus(
            "paid", payment_id="pay_5", amount_paid_paise=25000
        )
        PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        assert services.expire_stale_attempts() == 0
        attempt.refresh_from_db()
        assert attempt.status == "paid" and Order.objects.count() == 1

    def test_recently_expired_attempts_get_a_grace_period(self, api):
        _, attempt = started(api)
        PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now() - timedelta(minutes=2))
        assert services.expire_stale_attempts() == 0


class TestGatewayAdapter:
    def test_signature_verification(self, settings):
        body = b'{"event": "x"}'
        assert verify_webhook_signature(body, sign(body))
        assert not verify_webhook_signature(body, sign(b"other"))
        assert not verify_webhook_signature(body, "")
        settings.RAZORPAY_WEBHOOK_SECRET = ""
        assert not verify_webhook_signature(body, sign(body, "whsec_test"))

    def test_create_payment_link_request(self, settings):
        settings.SITE_URL = "https://shop.example.com/"
        gateway = RazorpayGateway("key", "secret", "https://api.razorpay.com/v1/")
        expires = timezone.now() + timedelta(minutes=30)
        reply = mock.Mock(ok=True, json=lambda: {"id": "plink_1", "short_url": "https://rzp.io/i/abc"})
        with mock.patch("apps.payments.gateway.requests.request", return_value=reply) as call:
            link = gateway.create_payment_link(
                reference="ref123", amount_paise=50000, expires_at=expires, customer={"name": "A"}
            )
        assert (link.id, link.short_url) == ("plink_1", "https://rzp.io/i/abc")
        args, kwargs = call.call_args
        assert args == ("POST", "https://api.razorpay.com/v1/payment_links")
        assert kwargs["auth"] == ("key", "secret") and kwargs["timeout"]
        body = kwargs["json"]
        assert body["amount"] == 50000 and body["currency"] == "INR" and body["reference_id"] == "ref123"
        assert body["callback_url"] == "https://shop.example.com/checkout/payment?reference=ref123"
        assert body["callback_method"] == "get" and body["expire_by"] == int(expires.timestamp())
        assert body["notify"] == {"sms": False, "email": False}

    def test_errors_become_gateway_errors(self):
        gateway = RazorpayGateway("key", "secret", "https://x")
        with mock.patch("apps.payments.gateway.requests.request", side_effect=requests.Timeout):
            with pytest.raises(GatewayError):
                gateway.fetch_payment_link("plink_1")
        bad = mock.Mock(ok=False, status_code=400, text="bad")
        with mock.patch("apps.payments.gateway.requests.request", return_value=bad):
            with pytest.raises(GatewayError):
                gateway.refund("pay_1", 100)
        garbage = mock.Mock(ok=True, json=mock.Mock(side_effect=ValueError))
        with mock.patch("apps.payments.gateway.requests.request", return_value=garbage):
            with pytest.raises(GatewayError):
                gateway.fetch_payment_link("plink_1")
        incomplete = mock.Mock(ok=True, json=lambda: {})
        with mock.patch("apps.payments.gateway.requests.request", return_value=incomplete):
            with pytest.raises(GatewayError):
                gateway.create_payment_link(
                    reference="r", amount_paise=1, expires_at=timezone.now(), customer={}
                )

    def test_fetch_and_refund_parsing(self):
        gateway = RazorpayGateway("key", "secret", "https://x")
        paid = {
            "status": "paid",
            "amount_paid": 25000,
            "payments": [{"payment_id": "pay_1", "status": "captured"}],
        }
        with mock.patch(
            "apps.payments.gateway.requests.request", return_value=mock.Mock(ok=True, json=lambda: paid)
        ):
            assert gateway.fetch_payment_link("plink_1") == LinkStatus("paid", "pay_1", 25000)
        partial = {"status": "partially_paid", "amount_paid": 10, "payments": []}
        with mock.patch(
            "apps.payments.gateway.requests.request", return_value=mock.Mock(ok=True, json=lambda: partial)
        ):
            assert gateway.fetch_payment_link("plink_1").status == "created"
        with mock.patch(
            "apps.payments.gateway.requests.request",
            return_value=mock.Mock(ok=True, json=lambda: {"id": "rfnd_1"}),
        ) as call:
            assert gateway.refund("pay_1", 25000) == "rfnd_1"
        assert call.call_args.args == ("POST", "https://x/payments/pay_1/refund")
        assert call.call_args.kwargs["json"] == {"amount": 25000}

    def test_factory(self, settings):
        settings.PAYMENT_GATEWAY = "razorpay"
        settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = ""
        with pytest.raises(PaymentsNotConfigured):
            get_gateway()
        settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "k", "s"
        assert isinstance(get_gateway(), RazorpayGateway)


@pytest.mark.django_db
def test_payment_records_are_read_only_in_admin(client, api):
    _, attempt = started(api)
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    assert client.get("/admin/payments/paymentattempt/").status_code == 200
    assert client.get(f"/admin/payments/paymentattempt/{attempt.pk}/change/").status_code == 200
    assert client.get("/admin/payments/paymentattempt/add/").status_code == 403
    assert client.get("/admin/payments/webhookevent/").status_code == 200
    assert send_webhook(event_payload("payment_link.cancelled", attempt)).status_code == 200
