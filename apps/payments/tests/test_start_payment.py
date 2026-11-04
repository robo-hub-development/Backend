import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts.models import User
from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders.models import Order
from apps.payments.models import PaymentAttempt

from .conftest import client_for
from .helpers import ORDERS, fill_cart, live_line_ids, start, start_for


@pytest.mark.django_db
class TestStartOnlinePayment:
    def test_returns_a_redirect_url_and_holds_stock_without_creating_an_order(self, api, member, gateway):
        product = make_product(price=250, stock_quantity=5)
        res, attempt = start_for(api, (product, 2))

        assert res.status_code == 200
        assert res.data == {"redirectUrl": attempt.short_url}
        assert attempt.short_url.startswith("https://rzp.test/")
        assert attempt.status == "pending"
        assert attempt.user == member
        assert attempt.amount == 500
        assert attempt.gateway_link_id == "plink_1"
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 3  # held for the payment
        assert CartItem.objects.get().ordered_at is None  # still in the cart until paid

        (call,) = gateway.created
        assert call["reference"] == attempt.reference
        assert call["amount_paise"] == 50000
        assert call["customer"] == {
            "name": "Aarav Mehta",
            "email": "aarav@example.com",
            "contact": "+919876543210",
        }
        assert timedelta(minutes=29) < call["expires_at"] - timezone.now() <= timedelta(minutes=30)

    def test_online_orders_can_have_many_items_and_quantities(self, api):
        a, b = make_product(price=100, stock_quantity=9), make_product(price=40, stock_quantity=9)
        res, attempt = start_for(api, (a, 3), (b, 2))
        assert res.status_code == 200
        assert attempt.amount == 380
        assert attempt.items.count() == 2

    def test_only_listed_lines_are_paid_for(self, api):
        wanted, left = make_product(price=300), make_product(price=40)
        cart_id = fill_cart(api, (wanted, 1), (left, 1))
        only = [str(CartItem.objects.get(product=wanted).pk)]
        assert start(api, cart_id, cart_items=only).status_code == 200
        assert PaymentAttempt.objects.get().amount == 300

    def test_lines_already_being_paid_cannot_be_paid_or_ordered_again(self, api):
        product = make_product(stock_quantity=5)
        cart_id = fill_cart(api, (product, 1))
        assert start(api, cart_id).status_code == 200

        again = start(api, cart_id)
        assert again.status_code == 400
        assert "cartItems" in again.data["errors"]
        cod = api.post(
            ORDERS,
            {
                "cartId": cart_id,
                "cartItems": live_line_ids(cart_id),
                "address": {
                    "fullName": "A",
                    "mobile": "9876543210",
                    "line1": "x",
                    "city": "c",
                    "state": "s",
                    "pincode": "560001",
                },
                "paymentMethod": "cod",
            },
            format="json",
        )
        assert cod.status_code == 400
        assert PaymentAttempt.objects.count() == 1
        assert not Order.objects.exists()
        product.refresh_from_db()
        assert product.stock_quantity == 4  # taken once

    def test_gateway_failure_releases_everything(self, api, gateway):
        gateway.fail_create = True
        product = make_product(stock_quantity=5)
        res, attempt = start_for(api, (product, 2))
        assert res.status_code == 502
        assert res.data["message"] == "Could not start the payment. Please try again."
        assert attempt.status == "failed" and attempt.failure_reason == "gateway_error"
        product.refresh_from_db()
        assert product.stock_quantity == 5
        # The lines are free again: a retry works once the gateway recovers.
        gateway.fail_create = False
        assert start(api, attempt.cart_id).status_code == 200

    def test_not_configured_is_503_and_changes_nothing(self, api, settings):
        settings.PAYMENT_GATEWAY = "razorpay"
        settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = settings.RAZORPAY_WEBHOOK_SECRET = ""
        product = make_product(stock_quantity=5)
        res, attempt = start_for(api, (product, 1))
        assert res.status_code == 503
        assert attempt is None
        product.refresh_from_db()
        assert product.stock_quantity == 5

    def test_out_of_stock_is_rejected_without_an_attempt(self, api):
        res, attempt = start_for(api, (make_product(stock_quantity=0), 1))
        assert res.status_code == 400
        assert attempt is None

    def test_zero_priced_total_is_rejected(self, api):
        res, attempt = start_for(api, (make_product(price=0), 1))
        assert res.status_code == 400
        assert attempt is None

    def test_another_users_cart_is_rejected(self, api, other_member):
        theirs = fill_cart(client_for(other_member), (make_product(stock_quantity=4), 1))
        res = start(api, theirs, cart_items=live_line_ids(theirs))
        assert res.status_code == 400
        assert not PaymentAttempt.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_two_shoppers_cannot_both_pay_for_the_last_unit(member, other_member):
    product = make_product(stock_quantity=1)
    barrier = threading.Barrier(2)
    statuses = []

    def go(user):
        try:
            client = client_for(user)
            cart_id = fill_cart(client, (product, 1))
            barrier.wait()
            statuses.append(start(client, cart_id).status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=go, args=(u,)) for u in (member, other_member)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(statuses) == [200, 400]
    product.refresh_from_db()
    assert product.stock_quantity == 0
    assert PaymentAttempt.objects.count() == 1
    assert User.objects.count() == 2
