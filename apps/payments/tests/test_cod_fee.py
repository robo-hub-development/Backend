from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders import checkout
from apps.orders.models import CodTransitPolicy, Order
from apps.orders.pricing import quote_cod_fee
from apps.payments.models import Payment, PaymentTransaction

from .conftest import client_for
from .helpers import (
    ADDRESS,
    ORDERS,
    cod_order,
    fill_cart,
    live_cart_lines,
    live_line_ids,
    online_payment,
    pay,
    place_with,
    set_cod_fee,
    stock_of,
)

QUOTE = "/api/v1/checkout/quote"


@pytest.mark.django_db
class TestFeePolicy:
    def fee(self, subtotal=500, state="Karnataka", pincode="560001"):
        return quote_cod_fee(Decimal(subtotal), state=state, pincode=pincode)[0]

    def test_no_rule_means_no_fee(self):
        assert quote_cod_fee(Decimal(500), state="Karnataka", pincode="560001") == (Decimal(0), {})

    def test_fixed_default_rule(self):
        set_cod_fee(40)
        fee, snapshot = quote_cod_fee(Decimal(500), state="Goa", pincode="403001")
        assert fee == Decimal("40.00")
        assert snapshot["scope"] == "default" and snapshot["value"] == "40.00"

    def test_percent_rule_with_min_and_max_clamps(self):
        set_cod_fee(10, mode="percent", min_fee=Decimal(30), max_fee=Decimal(80))
        assert self.fee(100) == Decimal("30.00")  # 10% = 10, raised to the minimum
        assert self.fee(500) == Decimal("50.00")
        assert self.fee(2000) == Decimal("80.00")  # 10% = 200, capped
        assert self.fee(Decimal("333.33")) == Decimal("33.33")

    def test_percent_rounds_half_up_to_paise(self):
        set_cod_fee(Decimal("2.5"), mode="percent")
        assert self.fee(Decimal("10.10")) == Decimal("0.25")  # 0.2525 -> 0.25
        assert self.fee(Decimal("10.20")) == Decimal("0.26")  # 0.255 -> 0.26

    def test_most_specific_rule_wins(self):
        set_cod_fee(40)
        set_cod_fee(60, scope="state", match="Karnataka")
        set_cod_fee(90, scope="pincode_prefix", match="56")
        set_cod_fee(120, scope="pincode_prefix", match="5600")
        assert self.fee(pincode="560001") == 120  # longest matching prefix
        assert self.fee(pincode="561203") == 90
        assert self.fee(pincode="400001", state="  karnataka ") == 60  # state match ignores case and spaces
        assert self.fee(pincode="400001", state="Goa") == 40

    def test_order_value_bands(self):
        set_cod_fee(0, min_order_value=Decimal(0), max_order_value=Decimal(999))  # free COD below 1000
        set_cod_fee(60, min_order_value=Decimal(1000))
        assert self.fee(999) == 0 and self.fee(1000) == 60
        free, snapshot = quote_cod_fee(Decimal(500), state="x", pincode="1")
        assert free == 0 and snapshot["value"] == "0.00"  # a matching 0 rule is still a rule

    def test_inactive_and_future_rules_are_ignored_and_newest_wins_ties(self):
        set_cod_fee(99, is_active=False)
        set_cod_fee(88, effective_from=timezone.now() + timedelta(days=1))
        assert self.fee() == 0
        set_cod_fee(30, effective_from=timezone.now() - timedelta(days=5))
        set_cod_fee(35, effective_from=timezone.now() - timedelta(days=1))
        assert self.fee() == 35

    def test_policy_validation(self):
        with pytest.raises(ValidationError):
            CodTransitPolicy(scope="state", match="", mode="fixed", value=Decimal(10)).full_clean()
        with pytest.raises(ValidationError):
            CodTransitPolicy(
                scope="pincode_prefix", match="56a", mode="fixed", value=Decimal(10)
            ).full_clean()
        CodTransitPolicy(scope="default", match="ignored", mode="fixed", value=Decimal(10)).full_clean()

    def test_database_constraints(self):
        bad = [
            {"value": Decimal(-1)},
            {"mode": "percent", "value": Decimal(101)},
            {"scope": "state", "match": ""},
            {"scope": "default", "match": "x"},
            {"min_fee": Decimal(50), "max_fee": Decimal(10)},
            {"min_order_value": Decimal(500), "max_order_value": Decimal(100)},
        ]
        for fields in bad:
            with pytest.raises(IntegrityError), transaction.atomic():
                CodTransitPolicy.objects.create(**{"value": Decimal(10), **fields})


@pytest.mark.django_db
class TestQuote:
    def body(self, cart_id, method="cod", **address):
        return {
            "cartId": str(cart_id),
            "cartItems": live_line_ids(cart_id),
            "address": {"state": "Karnataka", "pincode": "560001", **address},
            "paymentMethod": method,
        }

    def test_cod_quote_splits_online_fee_and_cash(self, api):
        set_cod_fee(40)
        cart_id = fill_cart(api, (make_product(price=500), 1))
        res = api.post(QUOTE, self.body(cart_id), format="json")
        assert res.status_code == 200
        assert res.data["subtotal"] == 500 and res.data["total"] == 540
        assert res.data["payableNow"] == 40 and res.data["dueOnDelivery"] == 500
        assert res.data["charges"] == [
            {"kind": "cod_transit", "label": "Cash on delivery transit fee", "amount": 40}
        ]

    def test_quote_uses_the_zone_of_the_address(self, api):
        set_cod_fee(40)
        set_cod_fee(75, scope="state", match="Goa")
        cart_id = fill_cart(api, (make_product(price=500), 1))
        assert api.post(QUOTE, self.body(cart_id, state="Goa"), format="json").data["payableNow"] == 75

    def test_online_quote_has_no_fee_and_everything_payable_now(self, api):
        set_cod_fee(40)
        cart_id = fill_cart(api, (make_product(price=500), 2))
        res = api.post(QUOTE, self.body(cart_id, method="online"), format="json")
        assert (
            res.data["total"],
            res.data["payableNow"],
            res.data["dueOnDelivery"],
            res.data["charges"],
        ) == (
            1000,
            1000,
            0,
            [],
        )

    def test_quote_applies_the_one_piece_rule_and_ownership_and_creates_nothing(self, api, other_member):
        set_cod_fee(40)
        product = make_product(price=500, stock_quantity=5)
        two = fill_cart(api, (product, 2))
        assert api.post(QUOTE, self.body(two), format="json").status_code == 400
        theirs = fill_cart(client_for(other_member), (make_product(), 1))
        assert (
            api.post(
                QUOTE, self.body(theirs) | {"cartItems": live_line_ids(theirs)}, format="json"
            ).status_code
            == 400
        )
        assert not Order.objects.exists() and stock_of(product) == 5

    def test_quote_validation_and_authentication(self, api):
        from rest_framework.test import APIClient

        cart_id = fill_cart(api, (make_product(), 1))
        assert api.post(QUOTE, self.body(cart_id, pincode="12"), format="json").status_code == 400
        assert APIClient().post(QUOTE, self.body(cart_id), format="json").status_code == 401


@pytest.mark.django_db
class TestPlaceCodOrderWithFee:
    def test_with_a_fee_the_order_waits_for_the_online_fee_and_owes_the_items_in_cash(self, api, gateway):
        set_cod_fee(40)
        product = make_product(price=500, stock_quantity=5)
        res = place_with(api, (product, 1), method="cod")

        assert res.status_code == 201
        body = res.data["order"]
        assert (body["status"], body["paymentMethod"], body["paymentStatus"]) == (
            "awaiting_payment",
            "cod",
            "pending",
        )
        assert (body["subtotal"], body["total"], body["amountPaid"], body["amountDue"]) == (500, 540, 0, 540)
        assert body["charges"] == [
            {"kind": "cod_transit", "label": "Cash on delivery transit fee", "amount": 40}
        ]
        assert res.data["redirectUrl"].startswith("https://rzp.test/")

        order = Order.objects.get()
        fee_payment, cash_payment = (
            order.payments.get(purpose="cod_fee"),
            order.payments.get(purpose="cod_balance"),
        )
        assert (fee_payment.purpose, fee_payment.method, fee_payment.amount) == ("cod_fee", "online", 40)
        assert (cash_payment.purpose, cash_payment.method, cash_payment.amount, cash_payment.status) == (
            "cod_balance",
            "cash",
            500,
            "pending",
        )
        assert gateway.created[0]["amount_paise"] == 4000  # only the fee goes to the gateway
        assert stock_of(product) == 4
        charge = order.charges.get()
        assert charge.amount == 40 and charge.rule["scope"] == "default" and charge.rule["value"] == "40.00"

    def test_paying_the_fee_makes_the_order_pending_and_leaves_the_cash_due(self, api):
        set_cod_fee(40)
        _, order = cod_order(api, price=500)
        pay(online_payment(order))

        order.refresh_from_db()
        assert order.status == "pending"
        assert (order.amount_paid, order.total, order.amount_due) == (40, 540, 500)
        assert order.payment_status == "pending"  # the item price is still owed in cash
        assert live_cart_lines() == 0  # the cart line is consumed now that the order is committed
        assert [e.to_status for e in order.status_events.all()] == ["awaiting_payment", "pending"]

    def test_no_matching_rule_means_a_free_cod_order_that_is_pending_at_once(self, api, gateway):
        product = make_product(price=500, stock_quantity=5)
        res = place_with(api, (product, 1), method="cod")
        assert res.status_code == 201 and "reference" not in res.data and "redirectUrl" not in res.data
        order = Order.objects.get()
        assert (order.status, order.total, order.payment_due_by) == ("pending", 500, None)
        assert order.charges.count() == 0 and order.payments.get().method == "cash"
        assert gateway.created == [] and live_cart_lines() == 0 and stock_of(product) == 4

    def test_cod_still_allows_one_piece_only(self, api):
        set_cod_fee(40)
        product = make_product(price=500, stock_quantity=5)
        res = place_with(api, (product, 2), method="cod")
        assert res.status_code == 400 and not Order.objects.exists() and stock_of(product) == 5

    def test_editing_the_policy_later_does_not_change_existing_orders(self, api):
        policy = set_cod_fee(40)
        _, order = cod_order(api)
        policy.value = Decimal(500)
        policy.save()
        order.refresh_from_db()
        assert order.charges_total == 40 and order.charges.get().amount == 40

    def test_unpaid_fee_order_can_be_abandoned_and_the_cart_is_intact(self, api):
        set_cod_fee(40)
        product, order = cod_order(api, stock=5)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")
        assert res.status_code == 200 and res.data["status"] == "cancelled"
        assert stock_of(product) == 5 and live_cart_lines() == 1
        assert order.payments.filter(status="cancelled").count() == 1  # the cash payment is dropped
        assert not PaymentTransaction.objects.exists()

    def test_gateway_not_configured_blocks_a_fee_order_but_not_a_free_one(self, api, settings):
        settings.PAYMENT_GATEWAY = "razorpay"
        settings.RAZORPAY_KEY_ID = settings.RAZORPAY_KEY_SECRET = settings.RAZORPAY_WEBHOOK_SECRET = ""
        product = make_product(price=500, stock_quantity=5)
        assert place_with(api, (product, 1), method="cod").status_code == 201  # no fee, nothing online needed
        set_cod_fee(40)
        other = make_product(price=500, stock_quantity=5)
        res = place_with(api, (other, 1), method="cod")
        assert res.status_code == 503 and stock_of(other) == 5 and Order.objects.count() == 1


@pytest.mark.django_db
class TestCodMoneyRules:
    def paid_fee_order(self, api, gateway=None):
        set_cod_fee(40)
        product, order = cod_order(api, price=500, stock=5)
        pay(online_payment(order), payment_id="pay_fee")
        order.refresh_from_db()
        return product, order

    def test_cancel_before_dispatch_refunds_the_fee(self, api, gateway):
        product, order = self.paid_fee_order(api)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")

        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"], res.data["amountPaid"]) == (
            "cancelled",
            "refund_in_progress",
            40,
        )
        assert gateway.refunds == [{"payment_id": "pay_fee", "amount_paise": 4000}]
        assert stock_of(product) == 5
        fee, cash = order.payments.get(purpose="cod_fee"), order.payments.get(purpose="cod_balance")
        assert (fee.status, cash.status) == ("refund_in_progress", "cancelled")
        assert not PaymentTransaction.objects.filter(kind="refund").exists()

    def test_a_failed_refund_keeps_the_cancellation_and_stays_visible(self, api, gateway):
        gateway.fail_refund = True
        _, order = self.paid_fee_order(api)
        res = api.post(f"{ORDERS}/{order.pk}/cancel")
        assert res.status_code == 200
        assert (res.data["status"], res.data["paymentStatus"], res.data["amountPaid"]) == (
            "cancelled",
            "pending",
            40,
        )
        assert order.payments.get(purpose="cod_fee").status == "captured"  # still refundable by hand

    def test_customer_refusing_delivery_forfeits_the_fee(self, api, gateway):
        _, order = self.paid_fee_order(api)
        Order.objects.filter(pk=order.pk).update(status="shipped")
        checkout.mark_return_to_origin(order.pk)

        order.refresh_from_db()
        assert order.status == "rto" and order.amount_due == 0
        assert order.amount_paid == 40  # the fee stays with us to cover the transit
        assert gateway.refunds == []
        assert order.payments.get(purpose="cod_balance").status == "uncollectable"
        assert order.payments.get(purpose="cod_fee").status == "captured"

    def test_delivery_collects_the_cash_and_settles_the_order(self, api):
        _, order = self.paid_fee_order(api)
        Order.objects.filter(pk=order.pk).update(status="shipped")
        checkout.mark_delivered(order.pk)

        order.refresh_from_db()
        assert order.status == "delivered"
        assert (order.amount_paid, order.total, order.amount_due, order.payment_status) == (
            540,
            540,
            0,
            "paid",
        )
        cash = order.payments.get(purpose="cod_balance")
        assert cash.status == "collected"
        assert cash.transactions.get().amount == 500

    def test_cod_without_a_fee_is_settled_at_delivery_too(self, api):
        _, order = cod_order(api, price=500)  # no policy: free COD
        Order.objects.filter(pk=order.pk).update(status="shipped")
        checkout.mark_delivered(order.pk)
        order.refresh_from_db()
        assert (order.payment_status, order.amount_paid, order.amount_due) == ("paid", 500, 0)

    def test_dispatched_orders_cannot_be_cancelled_by_the_customer(self, api, gateway):
        _, order = self.paid_fee_order(api)
        Order.objects.filter(pk=order.pk).update(status="shipped")
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 400
        assert gateway.refunds == []

    def test_a_pending_order_the_staff_have_not_accepted_is_still_cancellable_with_refund(self, api, gateway):
        _, order = self.paid_fee_order(api)
        assert order.status == "pending"
        assert api.post(f"{ORDERS}/{order.pk}/cancel").status_code == 200
        assert len(gateway.refunds) == 1


@pytest.mark.django_db
def test_amount_fields_are_consistent_for_a_fresh_fee_order(api):
    set_cod_fee(40)
    _, order = cod_order(api, price=500)
    assert order.total == order.subtotal + order.charges_total == Decimal(540)
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(total=Decimal(999))  # breaks total = subtotal + charges
    with pytest.raises(IntegrityError), transaction.atomic():
        Order.objects.filter(pk=order.pk).update(amount_paid=Decimal(9999))  # more than the order is worth
    assert CartItem.objects.count() == 1 and Payment.objects.count() == 2
    assert ADDRESS["state"] == "Karnataka" and fill_cart is not None  # helpers stay importable
