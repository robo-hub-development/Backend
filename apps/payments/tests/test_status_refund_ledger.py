from datetime import timedelta
from unittest import mock

import pytest
import requests
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orders import services as order_services
from apps.orders.models import Order, OrderStatusEvent
from apps.payments import services
from apps.payments.gateway import (
    GatewayError,
    LinkStatus,
    PaymentsNotConfigured,
    RazorpayGateway,
    get_gateway,
    verify_checkout_signature,
    verify_webhook_signature,
)
from apps.payments.models import Payment, PaymentTransaction

from .conftest import client_for
from .helpers import (
    ORDERS,
    checkout_signature,
    live_cart_lines,
    online_order,
    pay,
    send_webhook,
    sign,
    stock_of,
)

VERIFY = "/api/v1/payments/verify"


def status_url(payment):
    return f"/api/v1/payments/{payment.reference}"


@pytest.mark.django_db
class TestPaymentStatus:
    def test_pending_while_nothing_has_happened(self, api):
        _, order, payment = online_order(api)
        res = api.get(status_url(payment))
        assert res.status_code == 200 and res.data["status"] == "pending"
        assert res.data["order"]["id"] == str(order.pk) and res.data["order"]["status"] == "awaiting_payment"

    def test_success_with_the_order_after_the_webhook(self, api):
        _, _, payment = online_order(api, price=250)
        pay(payment)
        res = api.get(status_url(payment))
        assert res.data["status"] == "success"
        assert (
            res.data["order"]["status"],
            res.data["order"]["paymentStatus"],
            res.data["order"]["total"],
        ) == (
            "confirmed",
            "paid",
            250,
        )

    def test_a_late_webhook_is_covered_by_asking_the_gateway(self, api, gateway):
        _, order, payment = online_order(api, price=250)
        gateway.links[payment.gateway_link_id] = LinkStatus(
            "paid", payment_id="pay_9", amount_paid_paise=25000
        )
        first, second = api.get(status_url(payment)), api.get(status_url(payment))
        assert first.data["status"] == second.data["status"] == "success"
        assert PaymentTransaction.objects.count() == 1
        payment.refresh_from_db()
        assert payment.gateway_payment_id == "pay_9"

    def test_gateway_reports_the_link_expired_or_cancelled(self, api, gateway):
        product, order, payment = online_order(api)
        gateway.links[payment.gateway_link_id] = LinkStatus("expired")
        assert api.get(status_url(payment)).data["status"] == "failed"
        order.refresh_from_db()
        assert order.status == "awaiting_payment" and stock_of(product) == 4  # still retryable

    def test_gateway_down_still_answers_pending(self, api, gateway):
        _, _, payment = online_order(api)
        gateway.fail_fetch = True
        res = api.get(status_url(payment))
        assert res.status_code == 200 and res.data["status"] == "pending"

    def test_money_that_cannot_be_used_reads_as_failed(self, api):
        _, _, payment = online_order(api)
        pay(payment, amount_paise=1)
        assert api.get(status_url(payment)).data["status"] == "failed"

    def test_only_the_owner_can_see_a_payment(self, api, other_member):
        _, _, payment = online_order(api)
        assert client_for(other_member).get(status_url(payment)).status_code == 404
        assert api.get("/api/v1/payments/" + "0" * 32).status_code == 404
        assert APIClient().get(status_url(payment)).status_code == 401


@pytest.mark.django_db
class TestVerifyCheckout:
    def body(self, payment, payment_id="pay_v1", **override):
        data = {
            "reference": payment.reference,
            "razorpayOrderId": payment.gateway_order_id,
            "razorpayPaymentId": payment_id,
            "razorpaySignature": checkout_signature(payment.gateway_order_id, payment_id),
        }
        return {**data, **override}

    def test_valid_signature_and_a_captured_payment_returns_the_confirmed_order(self, api, gateway):
        _, order, payment = online_order(api, price=250, flow="checkout")
        gateway.orders[payment.gateway_order_id] = LinkStatus(
            "paid", payment_id="pay_v1", amount_paid_paise=25000
        )
        res = api.post(VERIFY, self.body(payment), format="json")
        assert res.status_code == 200 and res.data["status"] == "success"
        assert res.data["order"]["status"] == "confirmed"
        assert (
            api.post(VERIFY, self.body(payment), format="json").data["order"]["id"] == res.data["order"]["id"]
        )
        assert PaymentTransaction.objects.count() == 1

    def test_the_browser_cannot_mark_a_payment_paid(self, api):
        _, order, payment = online_order(api, flow="checkout")
        res = api.post(VERIFY, self.body(payment), format="json")
        assert res.status_code == 200 and res.data["status"] == "pending"
        order.refresh_from_db()
        assert order.status == "awaiting_payment"

    def test_bad_or_mismatched_signatures_are_rejected(self, api, gateway):
        _, order, payment = online_order(api, flow="checkout")
        gateway.orders[payment.gateway_order_id] = LinkStatus(
            "paid", payment_id="pay_v1", amount_paid_paise=25000
        )
        bad = api.post(VERIFY, self.body(payment, razorpaySignature="0" * 64), format="json")
        assert bad.status_code == 400 and "razorpaySignature" in bad.data["errors"]
        other = self.body(payment, razorpayPaymentId="pay_other")
        other["razorpaySignature"] = checkout_signature(payment.gateway_order_id, "pay_v1")
        assert api.post(VERIFY, other, format="json").status_code == 400
        order.refresh_from_db()
        assert order.status == "awaiting_payment"

    def test_other_users_wrong_orders_and_link_payments_are_404(self, api, other_member):
        _, _, payment = online_order(api, flow="checkout")
        assert client_for(other_member).post(VERIFY, self.body(payment), format="json").status_code == 404
        assert (
            api.post(VERIFY, self.body(payment, razorpayOrderId="order_x"), format="json").status_code == 404
        )
        assert api.post(VERIFY, self.body(payment, reference="0" * 32), format="json").status_code == 404

    def test_link_payments_cannot_be_verified_as_checkout(self, api):
        _, _, link_payment = online_order(api, flow="link")
        body = {
            "reference": link_payment.reference,
            "razorpayOrderId": "order_1",
            "razorpayPaymentId": "p",
            "razorpaySignature": checkout_signature("order_1", "p"),
        }
        assert api.post(VERIFY, body, format="json").status_code == 404

    def test_requires_authentication_and_all_fields(self, api):
        _, _, payment = online_order(api, flow="checkout")
        assert APIClient().post(VERIFY, self.body(payment), format="json").status_code == 401
        assert api.post(VERIFY, {"reference": payment.reference}, format="json").status_code == 400

    def test_status_polling_settles_a_captured_checkout_payment(self, api, gateway):
        _, order, payment = online_order(api, price=250, flow="checkout")
        gateway.orders[payment.gateway_order_id] = LinkStatus(
            "paid", payment_id="pay_9", amount_paid_paise=25000
        )
        assert api.get(status_url(payment)).data["status"] == "success"


@pytest.mark.django_db
class TestOnlineRefund:
    def paid(self, api, **kw):
        product, order, payment = online_order(api, **kw)
        pay(payment, payment_id="pay_77")
        order.refresh_from_db()
        return product, order, payment

    def test_cancelling_a_paid_online_order_refunds_it_and_restores_stock(self, api, gateway):
        product, order, payment = self.paid(api, price=250)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")
        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"], res.data["amountPaid"]) == (
            "cancelled",
            "refund_in_progress",
            250,  # nothing has come back yet: the ledger is only written once the gateway confirms
        )
        assert gateway.refunds == [{"payment_id": "pay_77", "amount_paise": 25000}]
        payment.refresh_from_db()
        assert (payment.status, payment.gateway_refund_id) == ("refund_in_progress", "rfnd_1")
        assert not payment.transactions.filter(kind="refund").exists()
        assert stock_of(product) == 5

    def test_a_failed_refund_does_not_undo_the_cancellation(self, api, gateway):
        gateway.fail_refund = True
        product, order, payment = self.paid(api)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")
        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"]) == (
            "cancelled",
            "paid",
        )  # staff refund by hand
        payment.refresh_from_db()
        assert payment.status == "captured" and not payment.transactions.filter(kind="refund").exists()
        assert stock_of(product) == 5

    def test_cancelling_twice_refunds_once(self, api, gateway):
        _, order, _ = self.paid(api)
        api.post(f"{ORDERS}/{order.pk}/cancel")
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 400
        assert len(gateway.refunds) == 1

    def test_shipped_online_orders_cannot_be_cancelled_or_refunded(self, api, gateway):
        _, order, _ = self.paid(api)
        Order.objects.filter(pk=order.pk).update(status="shipped")
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 400
        assert gateway.refunds == []

    def test_cancelling_an_unpaid_online_order_refunds_nothing(self, api, gateway):
        product, order, _ = online_order(api)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")
        assert res.data["status"] == "cancelled" and gateway.refunds == []
        assert stock_of(product) == 5 and live_cart_lines() == 1
        assert order.payments.get().status == "failed"

    def test_unpaid_orders_of_other_users_cannot_be_cancelled(self, api, other_member):
        _, order, _ = online_order(client_for(other_member))
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 404


def refund_payload(event, refund_id="rfnd_1", payment_id="pay_77"):
    return {
        "event": event,
        "payload": {
            "refund": {"entity": {"id": refund_id, "payment_id": payment_id}},
            "payment": {"entity": {"id": payment_id}},
        },
    }


@pytest.mark.django_db
class TestRefundLifecycle:
    """Cancelling starts the refund ("in progress"); it is "refunded" only once Razorpay confirms it."""

    def cancelled(self, api, **kw):
        _, order, payment = online_order(api, **kw)
        pay(payment, payment_id="pay_77")
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 200
        payment.refresh_from_db()
        order.refresh_from_db()
        return order, payment

    def test_cancelling_leaves_the_refund_in_progress_for_the_customer(self, api):
        order, payment = self.cancelled(api)
        assert (order.status, order.payment_status) == ("cancelled", "refund_in_progress")
        detail = api.get(f"{ORDERS}/{order.pk}")
        assert detail.data["paymentStatus"] == "refund_in_progress"
        assert api.get(status_url(payment)).data["status"] == "success"

    def test_the_processed_webhook_completes_the_refund(self, api):
        order, payment = self.cancelled(api, price=250)
        res = send_webhook(refund_payload("refund.processed"), event_id="evt_rp")
        assert res.status_code == 200

        payment.refresh_from_db()
        order.refresh_from_db()
        assert (payment.status, order.payment_status, order.amount_paid) == ("refunded", "refunded", 0)
        refund = payment.transactions.get(kind="refund")
        assert refund.gateway_ref == "rfnd_1" and refund.amount == 250
        assert refund.parent == payment.transactions.get(kind="charge")
        assert refund.reason == "cancelled before dispatch"

    def test_a_repeated_webhook_does_not_refund_twice(self, api):
        order, payment = self.cancelled(api)
        send_webhook(refund_payload("refund.processed"), event_id="evt_rp")
        send_webhook(refund_payload("refund.processed"), event_id="evt_rp")  # same delivery again
        send_webhook(refund_payload("refund.processed"), event_id="evt_rp2")  # a different event, same refund
        assert payment.transactions.filter(kind="refund").count() == 1

    def test_a_failed_refund_returns_the_payment_to_staff(self, api):
        order, payment = self.cancelled(api)
        send_webhook(refund_payload("refund.failed"), event_id="evt_rf")
        payment.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == "captured" and not payment.transactions.filter(kind="refund").exists()
        assert order.payment_status == "paid"  # money is still with us: staff refund it by hand

    def test_an_unknown_refund_is_ignored(self, api):
        order, payment = self.cancelled(api)
        send_webhook(refund_payload("refund.processed", refund_id="rfnd_other"), event_id="evt_x")
        payment.refresh_from_db()
        assert payment.status == "refund_in_progress"

    def test_reconcile_completes_a_refund_whose_webhook_never_came(self, api, gateway):
        order, payment = self.cancelled(api)
        gateway.refund_status = "pending"
        assert services.reconcile_refunds() == 0
        payment.refresh_from_db()
        assert payment.status == "refund_in_progress"

        gateway.refund_status = "processed"
        assert services.reconcile_refunds() == 1
        payment.refresh_from_db()
        order.refresh_from_db()
        assert (payment.status, order.payment_status) == ("refunded", "refunded")

    def test_reconcile_survives_a_gateway_outage(self, api, gateway):
        _, payment = self.cancelled(api)
        gateway.fail_fetch = True
        assert services.reconcile_refunds() == 0
        payment.refresh_from_db()
        assert payment.status == "refund_in_progress"

    def test_the_expiry_command_also_settles_refunds(self, api, gateway):
        _, payment = self.cancelled(api)
        gateway.refund_status = "processed"
        call_command("expire_payments")
        payment.refresh_from_db()
        assert payment.status == "refunded"


@pytest.mark.django_db
class TestExpiry:
    def test_unpaid_orders_past_their_window_expire_and_release_their_stock(self, api, other_member):
        stale_product, stale, stale_payment = online_order(api, stock=5)
        fresh_product, fresh, _ = online_order(client_for(other_member), stock=5)
        Order.objects.filter(pk=stale.pk).update(payment_due_by=timezone.now() - timedelta(minutes=30))

        call_command("expire_payments")

        stale.refresh_from_db()
        fresh.refresh_from_db()
        stale_payment.refresh_from_db()
        assert (stale.status, stale.payment_status) == ("expired", "failed")
        assert stale_payment.status == "expired"
        assert fresh.status == "awaiting_payment"
        assert (stock_of(stale_product), stock_of(fresh_product)) == (5, 4)
        assert live_cart_lines() == 2  # carts are intact
        assert stale.status_events.last().to_status == "expired"

    def test_an_order_that_was_actually_paid_is_confirmed_not_expired(self, api, gateway):
        _, order, payment = online_order(api, price=250)
        gateway.links[payment.gateway_link_id] = LinkStatus(
            "paid", payment_id="pay_5", amount_paid_paise=25000
        )
        Order.objects.filter(pk=order.pk).update(payment_due_by=timezone.now() - timedelta(hours=1))
        assert services.expire_unpaid_orders() == 0
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_recently_due_orders_get_a_grace_period(self, api):
        _, order, _ = online_order(api)
        Order.objects.filter(pk=order.pk).update(payment_due_by=timezone.now() - timedelta(minutes=2))
        assert services.expire_unpaid_orders() == 0


@pytest.mark.django_db
class TestLedgerInvariants:
    def test_every_status_change_is_audited_with_its_actor(self, api, member):
        _, order, payment = online_order(api)
        pay(payment)
        api.post(f"{ORDERS}/{order.pk}/cancel")
        events = list(order.status_events.values_list("from_status", "to_status", "actor"))
        assert events == [
            ("", "awaiting_payment", member.pk),
            ("awaiting_payment", "confirmed", None),  # the webhook is the system, not a person
            ("confirmed", "cancelled", member.pk),
        ]

    def test_illegal_transitions_are_refused_and_leave_no_trace(self, api):
        _, order, _ = online_order(api)
        with pytest.raises(order_services.InvalidTransition):
            order_services.transition(order, Order.Status.SHIPPED)
        order.refresh_from_db()
        assert order.status == "awaiting_payment" and order.status_events.count() == 1

    def test_only_one_pending_online_payment_per_order(self, api):
        _, order, payment = online_order(api)
        with pytest.raises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=order, purpose="full", method="online", amount=10, flow="link")

    def test_ledger_rules_in_the_database(self, api):
        _, order, payment = online_order(api)
        pay(payment, payment_id="pay_dup")
        charge = payment.transactions.get()
        with pytest.raises(IntegrityError), transaction.atomic():  # a gateway id can be recorded once
            PaymentTransaction.objects.create(payment=payment, kind="charge", amount=1, gateway_ref="pay_dup")
        with pytest.raises(IntegrityError), transaction.atomic():  # a refund must say which charge it repays
            PaymentTransaction.objects.create(payment=payment, kind="refund", amount=1, gateway_ref="r1")
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransaction.objects.create(payment=payment, kind="charge", amount=0, parent=charge)
        with pytest.raises(IntegrityError), transaction.atomic():  # cash only for the COD balance
            Payment.objects.create(order=order, purpose="full", method="cash", amount=5)

    def test_order_summary_is_the_net_of_the_ledger(self, api):
        _, order, payment = online_order(api, price=250)
        pay(payment)
        order.refresh_from_db()
        assert order.amount_paid == payment.transactions.get().amount == 250

    def test_orders_with_payments_cannot_be_deleted(self, api):
        _, order, _ = online_order(api)
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError), transaction.atomic():
            order.delete()


@pytest.mark.django_db
def test_order_and_payment_records_are_read_only_in_admin(client, api):
    _, order, payment = online_order(api)
    pay(payment)
    admin = User.objects.create_superuser(
        email="root@example.com", name="Root", mobile="9000000001", password="s3cret-pass-1"
    )
    client.force_login(admin)
    assert client.get("/admin/orders/order/").status_code == 200
    page = client.get(f"/admin/orders/order/{order.pk}/change/")
    assert page.status_code == 200 and order.order_number.encode() in page.content
    assert client.get("/admin/orders/order/add/").status_code == 403
    assert client.get("/admin/payments/payment/").status_code == 200
    assert client.get(f"/admin/payments/payment/{payment.pk}/change/").status_code == 200
    assert client.get("/admin/payments/payment/add/").status_code == 403
    assert client.get("/admin/payments/webhookevent/").status_code == 200
    # Fee rules are the one thing staff edit directly.
    assert client.get("/admin/orders/codtransitpolicy/add/").status_code == 200
    assert OrderStatusEvent.objects.filter(order=order).count() == 2


class TestGatewayAdapter:
    def test_signature_verification(self, settings):
        body = b'{"event": "x"}'
        assert verify_webhook_signature(body, sign(body))
        assert not verify_webhook_signature(body, sign(b"other"))
        assert not verify_webhook_signature(body, "")
        settings.RAZORPAY_WEBHOOK_SECRET = ""
        assert not verify_webhook_signature(body, sign(body, "whsec_test"))

    def test_checkout_signature(self):
        good = checkout_signature("order_1", "pay_1")
        assert verify_checkout_signature("order_1", "pay_1", good)
        assert not verify_checkout_signature("order_1", "pay_2", good)
        assert not verify_checkout_signature("order_1", "pay_1", "")

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
                gateway.create_order(reference="r", amount_paise=1)
            with pytest.raises(GatewayError):
                gateway.create_payment_link(
                    reference="r", amount_paise=1, expires_at=timezone.now(), customer={}
                )

    def test_parsing_and_requests(self):
        gateway = RazorpayGateway("rzp_key", "secret", "https://x")
        replies = {
            "/payment_links/plink_1": {
                "status": "paid",
                "amount_paid": 25000,
                "payments": [{"payment_id": "pay_1", "status": "captured"}],
            },
            "/orders": {"id": "order_9"},
            "/orders/order_9/payments": {
                "items": [
                    {"id": "pay_a", "status": "authorized", "amount": 1},
                    {"id": "pay_b", "status": "captured", "amount": 25000},
                ]
            },
            "/payments/pay_b/capture": {"id": "pay_b"},
            "/payments/pay_b/refund": {"id": "rfnd_1"},
        }

        def fake(method, url, **kwargs):
            return mock.Mock(ok=True, json=lambda: replies[url.removeprefix("https://x")])

        with mock.patch("apps.payments.gateway.requests.request", side_effect=fake) as call:
            assert gateway.fetch_payment_link("plink_1") == LinkStatus("paid", "pay_1", 25000)
            assert gateway.create_order(reference="ref", amount_paise=25000).id == "order_9"
            assert gateway.fetch_order("order_9") == LinkStatus("paid", "pay_b", 25000)
            gateway.capture("pay_b", 25000)
            assert gateway.refund("pay_b", 25000) == "rfnd_1"
        created = call.call_args_list[1]
        assert created.args == ("POST", "https://x/orders")
        assert created.kwargs["json"] == {
            "amount": 25000,
            "currency": "INR",
            "receipt": "ref",
            "notes": {"reference": "ref"},
        }
        assert call.call_args_list[3].kwargs["json"] == {"amount": 25000, "currency": "INR"}
        assert call.call_args_list[4].kwargs["json"] == {"amount": 25000}
        assert gateway.public_key == "rzp_key"

    def test_partially_paid_and_authorized_only_are_not_paid_yet(self):
        gateway = RazorpayGateway("k", "s", "https://x")
        partial = mock.Mock(
            ok=True, json=lambda: {"status": "partially_paid", "amount_paid": 10, "payments": []}
        )
        with mock.patch("apps.payments.gateway.requests.request", return_value=partial):
            assert gateway.fetch_payment_link("plink_1").status == "created"
        authorized = mock.Mock(
            ok=True, json=lambda: {"items": [{"id": "p", "status": "authorized", "amount": 5}]}
        )
        with mock.patch("apps.payments.gateway.requests.request", return_value=authorized):
            assert gateway.fetch_order("order_1").status == "created"

    def test_factory(self, settings):
        settings.PAYMENT_GATEWAY = "razorpay"
        settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = ""
        with pytest.raises(PaymentsNotConfigured):
            get_gateway()
        settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "k", "s"
        assert isinstance(get_gateway(), RazorpayGateway)


@pytest.mark.django_db(transaction=True)
class TestLedgerMigration:
    """The data move from the previous payment model (PaymentAttempt) to orders + payments + ledger."""

    BEFORE = [("orders", "0002_link_to_cart"), ("payments", "0002_checkout_flow")]

    def migrate_back(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.BEFORE)
        return executor.loader.project_state(self.BEFORE).apps

    def migrate_forward(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        return executor.loader.project_state(executor.loader.graph.leaf_nodes()).apps

    def seed(self, old, *, attempts, orders):
        user = old.get_model("accounts", "User").objects.create(
            email="m@example.com", name="M", mobile="9000000099"
        )
        Order, Attempt = old.get_model("orders", "Order"), old.get_model("payments", "PaymentAttempt")
        made = {}
        for key, (method, status, pay_status) in orders.items():
            made[key] = Order.objects.create(
                user=user,
                number=7000 + len(made),
                status=status,
                payment_method=method,
                payment_status=pay_status,
                total=500,
                ship_full_name="A",
                ship_mobile="9876543210",
                ship_line1="x",
                ship_city="c",
                ship_state="s",
                ship_pincode="560001",
            )
        for key, fields in attempts.items():
            Attempt.objects.create(
                user=user,
                order=made.get(key),
                amount=500,
                address={},
                expires_at=timezone.now(),
                reference=f"ref{key}".ljust(32, "0"),
                **fields,
            )
        return made

    def test_paid_refunded_cod_and_delivered_orders_are_carried_over(self):
        old = self.migrate_back()
        self.seed(
            old,
            orders={
                "paid": ("online", "confirmed", "paid"),
                "refunded": ("online", "cancelled", "refunded"),
                "cod": ("cod", "pending", "pending"),
                "delivered": ("cod", "delivered", "pending"),
                "cancelled": ("cod", "cancelled", "pending"),
            },
            attempts={
                "paid": {"status": "paid", "gateway_payment_id": "pay_p", "flow": "link"},
                "refunded": {
                    "status": "paid",
                    "gateway_payment_id": "pay_r",
                    "refund_id": "rfnd_r",
                    "flow": "checkout",
                    "gateway_order_id": "order_r",
                },
                "gone": {"status": "failed"},  # a failed attempt had no order: nothing to carry over
            },
        )

        new = self.migrate_forward()
        Order, Payment, Tx = (
            new.get_model("orders", "Order"),
            new.get_model("payments", "Payment"),
            new.get_model("payments", "PaymentTransaction"),
        )
        by_number = {o.number: o for o in Order.objects.all()}
        paid, refunded, cod, delivered, cancelled = (by_number[n] for n in range(7000, 7005))

        assert (paid.subtotal, paid.total, paid.amount_paid) == (500, 500, 500)
        paid_payment = Payment.objects.get(order=paid)
        assert (paid_payment.status, paid_payment.purpose, paid_payment.flow) == ("captured", "full", "link")
        assert Tx.objects.get(payment=paid_payment).gateway_ref == "pay_p"

        refunded_payment = Payment.objects.get(order=refunded)
        assert refunded_payment.status == "refunded" and refunded.amount_paid == 0
        assert sorted(Tx.objects.filter(payment=refunded_payment).values_list("kind", flat=True)) == [
            "charge",
            "refund",
        ]

        assert Payment.objects.get(order=cod).status == "pending"
        assert Payment.objects.get(order=delivered).status == "collected"
        assert (delivered.amount_paid, delivered.payment_status) == (500, "paid")
        assert Payment.objects.get(order=cancelled).status == "cancelled"
        assert Payment.objects.filter(purpose="cod_balance").count() == 3

    def test_it_refuses_to_run_while_money_or_stock_is_still_in_flight(self):
        old = self.migrate_back()
        self.seed(
            old, orders={}, attempts={"stuck": {"status": "needs_refund", "gateway_payment_id": "pay_s"}}
        )
        executor = MigrationExecutor(connection)
        with pytest.raises(RuntimeError, match="refund"):
            executor.migrate(executor.loader.graph.leaf_nodes())
        # leave the schema where the other tests expect it
        old.get_model("payments", "PaymentAttempt").objects.all().delete()
        self.migrate_forward()
