import hashlib
import hmac
import json
from decimal import Decimal

from django.conf import settings
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.catalog.tests.conftest import make_product
from apps.orders.models import CodTransitPolicy, Order
from apps.orders.tests.test_orders_api import ADDRESS, fill_cart, live_line_ids  # noqa: F401
from apps.payments.models import Payment
from apps.payments.services import to_paise

ORDERS = "/api/v1/orders"
WEBHOOK = "/api/v1/payments/webhook"


def place(api, cart_id, *, method="online", flow="link", cart_items=None, address=None):
    body = {
        "cartId": str(cart_id),
        "cartItems": live_line_ids(cart_id) if cart_items is None else cart_items,
        "address": address or ADDRESS,
        "paymentMethod": method,
        "paymentFlow": flow,
    }
    return api.post(ORDERS, body, format="json")


def place_with(api, *lines, **kwargs):
    """Fill the cart with `lines` ((product, qty) pairs), then place the order."""
    return place(api, fill_cart(api, *lines), **kwargs)


def online_order(api, *, price=250, qty=1, stock=5, flow="link", product=None):
    """An online order waiting for payment. Returns (product, order, its online payment)."""
    product = product or make_product(price=price, stock_quantity=stock)
    res = place_with(api, (product, qty), flow=flow)
    assert res.status_code == 201, res.data
    order = Order.objects.get(pk=res.data["order"]["id"])
    return product, order, online_payment(order)


def online_payment(order) -> Payment:
    return order.payments.filter(method="online").order_by("created_at").last()


def set_cod_fee(value=40, *, mode="fixed", scope="default", match="", **extra):
    return CodTransitPolicy.objects.create(scope=scope, match=match, mode=mode, value=Decimal(value), **extra)


def cod_order(api, *, price=500, stock=5, product=None):
    """A COD order (a fee policy must exist for it to wait for payment). Returns (product, order)."""
    product = product or make_product(price=price, stock_quantity=stock)
    res = place_with(api, (product, 1), method="cod")
    assert res.status_code == 201, res.data
    return product, Order.objects.get(pk=res.data["order"]["id"])


def sign(body: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.RAZORPAY_WEBHOOK_SECRET).encode(), body, hashlib.sha256).hexdigest()


def link_payload(event, payment, *, payment_id="pay_1", amount_paise=None, link_id=None):
    return {
        "event": event,
        "payload": {
            "payment_link": {
                "entity": {"id": link_id or payment.gateway_link_id, "reference_id": payment.reference}
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": to_paise(payment.amount) if amount_paise is None else amount_paise,
                }
            },
        },
    }


def checkout_payload(event, payment, *, payment_id="pay_c1", amount_paise=None, order_id=None):
    order_id = order_id or payment.gateway_order_id
    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": to_paise(payment.amount) if amount_paise is None else amount_paise,
                    "status": "captured",
                }
            },
            "order": {"entity": {"id": order_id}},
        },
    }


def send_webhook(payload, *, event_id="evt_1", signature=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    return APIClient().generic(
        "POST",
        WEBHOOK,
        body,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=sign(body) if signature is None else signature,
        HTTP_X_RAZORPAY_EVENT_ID=event_id,
    )


def pay(payment, *, event_id="evt_paid", **kwargs):
    """Deliver the 'paid' webhook that matches how the payment was started."""
    if payment.flow == "checkout":
        payload = checkout_payload("payment.captured", payment, **kwargs)
    else:
        payload = link_payload("payment_link.paid", payment, **kwargs)
    return send_webhook(payload, event_id=event_id)


def checkout_signature(order_id, payment_id, secret="test_key_secret"):  # noqa: S107 - test-only value
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def stock_of(product) -> int:
    product.refresh_from_db()
    return product.stock_quantity


def live_cart_lines() -> int:
    return CartItem.objects.filter(ordered_at__isnull=True).count()
