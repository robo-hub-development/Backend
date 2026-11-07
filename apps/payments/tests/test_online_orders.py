import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order, OrderStatusEvent
from apps.payments.models import Payment

from .conftest import client_for
from .helpers import (
    ORDERS,
    fill_cart,
    live_cart_lines,
    live_line_ids,
    online_order,
    online_payment,
    place,
    place_with,
    stock_of,
)


@pytest.mark.django_db
class TestPlaceOnlineOrder:
    def test_creates_the_order_now_holds_stock_and_returns_the_payment_page(self, api, member, gateway):
        product = make_product(price=250, stock_quantity=5)
        res = place_with(api, (product, 2))

        assert res.status_code == 201
        order = Order.objects.get()
        payment = online_payment(order)
        assert res.data["reference"] == payment.reference
        assert res.data["redirectUrl"] == payment.short_url.rstrip() and payment.short_url.startswith(
            "https://rzp.test/"
        )
        body = res.data["order"]
        assert (body["status"], body["paymentStatus"], body["paymentMethod"]) == (
            "awaiting_payment",
            "pending",
            "online",
        )
        assert (body["subtotal"], body["total"], body["amountPaid"], body["amountDue"]) == (500, 500, 0, 500)
        assert body["charges"] == [] and body["canCancel"] is True
        assert body["payment"]["reference"] == payment.reference

        assert (order.user, order.payment_due_by is not None) == (member, True)
        assert (payment.purpose, payment.method, payment.status, payment.flow) == (
            "full",
            "online",
            "pending",
            "link",
        )
        assert payment.amount == 500 and payment.gateway_link_id == "plink_1"
        assert stock_of(product) == 3  # held for the order
        assert live_cart_lines() == 1  # the cart line stays in the cart until the order is paid
        (call,) = gateway.created
        assert call["amount_paise"] == 50000
        assert call["customer"] == {
            "name": "Aarav Mehta",
            "email": "aarav@example.com",
            "contact": "+919876543210",
        }
        assert timedelta(minutes=29) < call["expires_at"] - timezone.now() <= timedelta(minutes=30)
        assert list(OrderStatusEvent.objects.values_list("from_status", "to_status")) == [
            ("", "awaiting_payment")
        ]

    def test_unpaid_orders_are_visible_in_the_history_with_their_payment(self, api):
        _, order, payment = online_order(api)
        listed = api.get(ORDERS).data["items"]
        assert [o["id"] for o in listed] == [str(order.pk)]
        assert listed[0]["status"] == "awaiting_payment"
        assert listed[0]["payment"]["redirectUrl"] == payment.short_url
        assert api.get(f"{ORDERS}/{order.pk}").data["payment"]["reference"] == payment.reference

    def test_many_items_and_quantities_in_one_online_order(self, api):
        a, b = make_product(price=100, stock_quantity=9), make_product(price=40, stock_quantity=9)
        res = place_with(api, (a, 3), (b, 2))
        assert res.status_code == 201
        order = Order.objects.get()
        assert (order.subtotal, order.total, order.items.count()) == (380, 380, 2)
        assert online_payment(order).amount == 380

    def test_only_listed_cart_lines_are_ordered(self, api):
        from apps.cart.models import CartItem

        wanted, left = make_product(price=300), make_product(price=40)
        cart_id = fill_cart(api, (wanted, 1), (left, 1))
        only = [str(CartItem.objects.get(product=wanted).pk)]
        assert place(api, cart_id, cart_items=only).status_code == 201
        assert Order.objects.get().total == 300

    def test_lines_held_by_an_unpaid_order_cannot_be_ordered_again(self, api):
        product = make_product(stock_quantity=5)
        cart_id = fill_cart(api, (product, 1))
        assert place(api, cart_id).status_code == 201

        again = place(api, cart_id)
        assert again.status_code == 400 and "cartItems" in again.data["errors"]
        cod = place(api, cart_id, method="cod")
        assert cod.status_code == 400
        assert Order.objects.count() == 1
        assert stock_of(product) == 4  # taken once

    def test_gateway_failure_cancels_the_order_and_releases_everything(self, api, gateway):
        gateway.fail_create = True
        product = make_product(stock_quantity=5)
        res = place_with(api, (product, 2))
        assert res.status_code == 502
        order = Order.objects.get()
        assert order.status == "cancelled"
        assert order.payments.get().status == "failed"
        assert stock_of(product) == 5
        assert live_cart_lines() == 1
        assert [e.to_status for e in order.status_events.all()] == ["awaiting_payment", "cancelled"]
        gateway.fail_create = False
        assert place(api, order.cart_id).status_code == 201  # the same cart lines can be ordered again

    def test_not_configured_is_503_and_creates_nothing(self, api, settings):
        settings.PAYMENT_GATEWAY = "razorpay"
        settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = settings.RAZORPAY_WEBHOOK_SECRET = ""
        product = make_product(stock_quantity=5)
        res = place_with(api, (product, 1))
        assert res.status_code == 503
        assert not Order.objects.exists()
        assert stock_of(product) == 5

    def test_out_of_stock_inactive_and_free_items_are_rejected(self, api):
        assert place_with(api, (make_product(stock_quantity=0), 1)).status_code == 400
        assert not Order.objects.exists()

    def test_another_users_cart_is_rejected(self, api, other_member):
        theirs = fill_cart(client_for(other_member), (make_product(stock_quantity=4), 1))
        res = place(api, theirs, cart_items=live_line_ids(theirs))
        assert res.status_code == 400 and not Order.objects.exists()


@pytest.mark.django_db
class TestCheckoutFlow:
    def test_checkout_returns_the_parameters_razorpay_checkout_needs(self, api, gateway):
        product = make_product(price=250, stock_quantity=5)
        res = place_with(api, (product, 2), flow="checkout")
        payment = Payment.objects.get()
        assert res.status_code == 201
        assert res.data["reference"] == payment.reference
        assert res.data["razorpay"] == {
            "keyId": "rzp_test_fake",
            "orderId": "order_1",
            "amount": 50000,
            "currency": "INR",
        }
        assert "redirectUrl" not in res.data
        assert (payment.flow, payment.gateway_order_id, payment.gateway_link_id) == (
            "checkout",
            "order_1",
            None,
        )
        assert gateway.created == [] and gateway.created_orders[0]["amount_paise"] == 50000
        assert stock_of(product) == 3

    def test_unknown_flow_is_rejected(self, api):
        res = place_with(api, (make_product(), 1), flow="paypal")
        assert res.status_code == 400 and not Order.objects.exists()


@pytest.mark.django_db
class TestPayAgain:
    def test_returns_the_open_payment_unchanged(self, api):
        _, order, payment = online_order(api)
        res = api.post(f"{ORDERS}/{order.pk}/pay", {}, format="json")
        assert res.status_code == 200
        assert res.data["reference"] == payment.reference and res.data["redirectUrl"] == payment.short_url
        assert order.payments.count() == 1

    def test_starts_a_fresh_payment_when_the_link_failed_or_expired(self, api, gateway):
        _, order, first = online_order(api, price=250)
        Payment.objects.filter(pk=first.pk).update(status="expired")
        res = api.post(f"{ORDERS}/{order.pk}/pay", {"paymentFlow": "checkout"}, format="json")
        assert res.status_code == 200
        second = online_payment(order)
        assert second.pk != first.pk and second.flow == "checkout" and second.amount == 250
        assert res.data["razorpay"]["orderId"] == second.gateway_order_id
        assert order.payments.filter(status="pending").count() == 1

    def test_too_close_to_the_deadline_is_refused(self, api):
        _, order, first = online_order(api)
        Payment.objects.filter(pk=first.pk).update(status="expired")
        Order.objects.filter(pk=order.pk).update(payment_due_by=timezone.now() + timedelta(minutes=5))
        res = api.post(f"{ORDERS}/{order.pk}/pay", {}, format="json")
        assert res.status_code == 400
        assert order.payments.filter(status="pending").count() == 0

    def test_only_awaiting_orders_of_the_caller_can_be_paid(self, api, other_member):
        _, order, _ = online_order(api)
        assert client_for(other_member).post(f"{ORDERS}/{order.pk}/pay", {}, format="json").status_code == 404
        Order.objects.filter(pk=order.pk).update(status="confirmed")
        assert api.post(f"{ORDERS}/{order.pk}/pay", {}, format="json").status_code == 400

    def test_gateway_failure_keeps_the_order_for_another_try(self, api, gateway):
        _, order, first = online_order(api)
        Payment.objects.filter(pk=first.pk).update(status="failed")
        gateway.fail_create = True
        assert api.post(f"{ORDERS}/{order.pk}/pay", {}, format="json").status_code == 502
        order.refresh_from_db()
        assert order.status == "awaiting_payment"


@pytest.mark.django_db(transaction=True)
def test_two_shoppers_cannot_both_order_the_last_unit(member, other_member):
    product = make_product(stock_quantity=1)
    barrier = threading.Barrier(2)
    statuses = []

    def go(user):
        try:
            client = client_for(user)
            cart_id = fill_cart(client, (product, 1))
            barrier.wait()
            statuses.append(place(client, cart_id).status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=go, args=(u,)) for u in (member, other_member)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(statuses) == [201, 400]
    assert stock_of(product) == 0
    assert Order.objects.count() == 1
    assert User.objects.count() == 2
