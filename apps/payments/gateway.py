"""Payment gateway adapter: the rest of the code talks to PaymentGateway, never to Razorpay directly."""

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = (3.05, 10)  # connect, read seconds


class GatewayError(Exception):
    """The gateway rejected the call, timed out or answered nonsense."""


class PaymentsNotConfigured(Exception):
    """Gateway credentials are missing."""


@dataclass(frozen=True)
class PaymentLink:
    id: str
    short_url: str


@dataclass(frozen=True)
class GatewayOrder:
    id: str


@dataclass(frozen=True)
class LinkStatus:
    """State of a payment link or of a Razorpay order (status: created | paid | cancelled | expired)."""

    status: str
    payment_id: str = ""
    amount_paid_paise: int = 0


class PaymentGateway(ABC):
    @abstractmethod
    def create_payment_link(
        self, *, reference: str, amount_paise: int, expires_at, customer: dict
    ) -> PaymentLink:
        """Create a hosted payment page; `reference` comes back in webhooks and the return URL."""

    @abstractmethod
    def fetch_payment_link(self, link_id: str) -> LinkStatus:
        """Current state of a link, used when a webhook is late or missed."""

    @abstractmethod
    def create_order(self, *, reference: str, amount_paise: int) -> GatewayOrder:
        """Create a Razorpay Order for the Checkout popup (the "simple payment" flow)."""

    @abstractmethod
    def fetch_order(self, order_id: str) -> LinkStatus:
        """Whether a captured payment exists for a Razorpay Order."""

    @abstractmethod
    def capture(self, payment_id: str, amount_paise: int) -> None:
        """Capture an authorized payment (only needed when the account does not auto-capture)."""

    @abstractmethod
    def refund(self, payment_id: str, amount_paise: int) -> str:
        """Refund a captured payment; returns the refund id."""

    @abstractmethod
    def fetch_refund(self, refund_id: str) -> str:
        """State of a refund: "pending", "processed" or "failed" (used when a webhook is late or lost)."""

    @property
    @abstractmethod
    def public_key(self) -> str:
        """Key id the browser needs to open Checkout (public, unlike the secret)."""


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Razorpay signs the exact raw request body with the webhook secret (HMAC-SHA256, hex)."""
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Checkout returns HMAC-SHA256(order_id|payment_id, key secret); proves the pair belongs together."""
    secret = settings.RAZORPAY_KEY_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class RazorpayGateway(PaymentGateway):
    def __init__(self, key_id: str, key_secret: str, base_url: str):
        self._auth = (key_id, key_secret)
        self._base = base_url.rstrip("/")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = requests.request(
                method, f"{self._base}{path}", json=payload, auth=self._auth, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            logger.error("Razorpay %s %s failed: %s", method, path, exc.__class__.__name__)
            raise GatewayError("Could not reach the payment gateway.") from exc
        if not response.ok:
            # Log the status and Razorpay's error description, never credentials or customer data.
            logger.error("Razorpay %s %s -> %s %s", method, path, response.status_code, response.text[:300])
            raise GatewayError(f"Gateway answered {response.status_code}.")
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError("Gateway returned an invalid response.") from exc

    def create_payment_link(self, *, reference, amount_paise, expires_at, customer) -> PaymentLink:
        data = self._request(
            "POST",
            "/payment_links",
            {
                "amount": amount_paise,
                "currency": "INR",
                "reference_id": reference,
                "description": "Stem Robo Hub order",
                "customer": customer,
                "notify": {"sms": False, "email": False},  # the storefront redirects the shopper itself
                "reminder_enable": False,
                "callback_url": f"{settings.SITE_URL.rstrip('/')}/checkout/payment?reference={reference}",
                "callback_method": "get",
                "expire_by": int(expires_at.timestamp()),
            },
        )
        try:
            return PaymentLink(id=data["id"], short_url=data["short_url"])
        except KeyError as exc:
            raise GatewayError("Gateway response is missing the payment link.") from exc

    def fetch_payment_link(self, link_id: str) -> LinkStatus:
        data = self._request("GET", f"/payment_links/{link_id}")
        status = data.get("status", "created")
        captured = [p for p in data.get("payments") or [] if p.get("status") in (None, "captured")]
        return LinkStatus(
            status="created" if status == "partially_paid" else status,
            payment_id=captured[0].get("payment_id", "") if captured else "",
            amount_paid_paise=int(data.get("amount_paid") or 0),
        )

    def create_order(self, *, reference, amount_paise) -> GatewayOrder:
        data = self._request(
            "POST",
            "/orders",
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": reference,
                "notes": {"reference": reference},
            },
        )
        try:
            return GatewayOrder(id=data["id"])
        except KeyError as exc:
            raise GatewayError("Gateway response is missing the order id.") from exc

    def fetch_order(self, order_id: str) -> LinkStatus:
        data = self._request("GET", f"/orders/{order_id}/payments")
        captured = [p for p in data.get("items") or [] if p.get("status") == "captured"]
        if not captured:
            return LinkStatus(status="created")
        return LinkStatus(
            status="paid",
            payment_id=captured[0].get("id", ""),
            amount_paid_paise=int(captured[0].get("amount") or 0),
        )

    def capture(self, payment_id: str, amount_paise: int) -> None:
        self._request("POST", f"/payments/{payment_id}/capture", {"amount": amount_paise, "currency": "INR"})

    @property
    def public_key(self) -> str:
        return self._auth[0]

    def refund(self, payment_id: str, amount_paise: int) -> str:
        data = self._request("POST", f"/payments/{payment_id}/refund", {"amount": amount_paise})
        try:
            return data["id"]
        except KeyError as exc:
            raise GatewayError("Gateway response is missing the refund id.") from exc

    def fetch_refund(self, refund_id: str) -> str:
        data = self._request("GET", f"/refunds/{refund_id}")
        status = data.get("status")
        return status if status in ("pending", "processed", "failed") else "pending"


@dataclass
class FakeGateway(PaymentGateway):
    """In-memory gateway for tests and local experiments; records calls and can be told to fail."""

    links: dict = field(default_factory=dict)  # link_id -> LinkStatus
    created: list = field(default_factory=list)
    refunds: list = field(default_factory=list)
    orders: dict = field(default_factory=dict)  # razorpay order id -> LinkStatus
    created_orders: list = field(default_factory=list)
    captures: list = field(default_factory=list)
    fail_create: bool = False
    fail_fetch: bool = False
    fail_refund: bool = False
    fail_capture: bool = False
    refund_status: str = "pending"  # what fetch_refund reports
    public_key: str = "rzp_test_fake"

    def create_payment_link(self, *, reference, amount_paise, expires_at, customer) -> PaymentLink:
        if self.fail_create:
            raise GatewayError("fake create failure")
        link_id = f"plink_{len(self.created) + 1}"
        self.created.append(
            {
                "id": link_id,
                "reference": reference,
                "amount_paise": amount_paise,
                "expires_at": expires_at,
                "customer": customer,
            }
        )
        self.links[link_id] = LinkStatus(status="created")
        return PaymentLink(id=link_id, short_url=f"https://rzp.test/{link_id}")

    def fetch_payment_link(self, link_id: str) -> LinkStatus:
        if self.fail_fetch:
            raise GatewayError("fake fetch failure")
        return self.links[link_id]

    def create_order(self, *, reference, amount_paise) -> GatewayOrder:
        if self.fail_create:
            raise GatewayError("fake create failure")
        order_id = f"order_{len(self.created_orders) + 1}"
        self.created_orders.append({"id": order_id, "reference": reference, "amount_paise": amount_paise})
        self.orders[order_id] = LinkStatus(status="created")
        return GatewayOrder(id=order_id)

    def fetch_order(self, order_id: str) -> LinkStatus:
        if self.fail_fetch:
            raise GatewayError("fake fetch failure")
        return self.orders[order_id]

    def capture(self, payment_id: str, amount_paise: int) -> None:
        if self.fail_capture:
            raise GatewayError("fake capture failure")
        self.captures.append({"payment_id": payment_id, "amount_paise": amount_paise})

    def refund(self, payment_id: str, amount_paise: int) -> str:
        if self.fail_refund:
            raise GatewayError("fake refund failure")
        self.refunds.append({"payment_id": payment_id, "amount_paise": amount_paise})
        return f"rfnd_{len(self.refunds)}"

    def fetch_refund(self, refund_id: str) -> str:
        if self.fail_fetch:
            raise GatewayError("fake fetch failure")
        return self.refund_status


_fake_gateway = FakeGateway()


def reset_fake_gateway() -> FakeGateway:
    global _fake_gateway
    _fake_gateway = FakeGateway()
    return _fake_gateway


def get_gateway() -> PaymentGateway:
    if settings.PAYMENT_GATEWAY == "fake":
        return _fake_gateway
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET and settings.RAZORPAY_WEBHOOK_SECRET):
        raise PaymentsNotConfigured
    return RazorpayGateway(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET, settings.RAZORPAY_API_BASE)
