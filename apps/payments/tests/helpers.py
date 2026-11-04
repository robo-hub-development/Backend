import hashlib
import hmac
import json

from django.conf import settings
from rest_framework.test import APIClient

from apps.orders.tests.test_orders_api import ADDRESS, fill_cart, live_line_ids  # noqa: F401
from apps.payments.models import PaymentAttempt

ORDERS = "/api/v1/orders"
WEBHOOK = "/api/v1/payments/webhook"


def start(api, cart_id, *, cart_items=None):
    body = {
        "cartId": str(cart_id),
        "cartItems": live_line_ids(cart_id) if cart_items is None else cart_items,
        "address": ADDRESS,
        "paymentMethod": "online",
    }
    return api.post(ORDERS, body, format="json")


def start_for(api, *lines):
    """Fill the cart with `lines` and start an online payment; returns (response, attempt or None)."""
    res = start(api, fill_cart(api, *lines))
    return res, PaymentAttempt.objects.order_by("created_at").last()


def sign(body: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.RAZORPAY_WEBHOOK_SECRET).encode(), body, hashlib.sha256).hexdigest()


def event_payload(event, attempt, *, payment_id="pay_1", amount_paise=None, link_id=None):
    from apps.payments.services import to_paise

    return {
        "event": event,
        "payload": {
            "payment_link": {
                "entity": {"id": link_id or attempt.gateway_link_id, "reference_id": attempt.reference}
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": to_paise(attempt.amount) if amount_paise is None else amount_paise,
                }
            },
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


def pay(attempt, *, event_id="evt_paid", **kw):
    return send_webhook(event_payload("payment_link.paid", attempt, **kw), event_id=event_id)
