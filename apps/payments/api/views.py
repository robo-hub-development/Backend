import hashlib
import json
import logging

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api_docs import UNAUTHORIZED, VALIDATION_ERROR, error_response
from apps.orders import selectors as order_selectors
from apps.orders.api.serializers import OrderSerializer
from apps.payments import services
from apps.payments.gateway import verify_webhook_signature
from apps.payments.models import Payment

from .serializers import PaymentStatusSerializer, VerifyCheckoutSerializer, WebhookResultSerializer

logger = logging.getLogger(__name__)

NOT_FOUND = error_response("No such payment for the signed-in user.")
BAD_WEBHOOK = error_response("Missing/invalid signature or malformed body.")

# What the storefront's payment-return page understands.
STATUS_FOR_FRONTEND = {
    Payment.Status.PENDING: "pending",
    Payment.Status.CAPTURED: "success",
    Payment.Status.REFUND_IN_PROGRESS: "success",  # same: the payment itself succeeded
    Payment.Status.REFUNDED: "success",  # it did succeed; the order was cancelled and refunded afterwards
    Payment.Status.FAILED: "failed",
    Payment.Status.EXPIRED: "failed",
    Payment.Status.NEEDS_REFUND: "failed",  # paid but unusable; staff are refunding it
}


def _status_body(request, payment) -> dict:
    order = order_selectors.get_user_order(request.user, payment.order_id)
    return {
        "status": STATUS_FOR_FRONTEND[payment.status],
        "order": OrderSerializer(order, context={"request": request}).data,
    }


class PaymentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Outcome of an online payment",
        description="Called from the payment return page. While `pending` it asks the gateway directly, so "
        "a late webhook does not leave the shopper waiting. The order is always returned.",
        responses={200: PaymentStatusSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def get(self, request, reference):
        # Scoped to the caller: another user's reference is indistinguishable from an unknown one.
        payment = Payment.objects.filter(order__user=request.user, reference=reference).first()
        if payment is None:
            raise NotFound
        return Response(_status_body(request, services.reconcile(payment)))


class VerifyCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Confirm a Razorpay Checkout payment",
        description="Call from Checkout's success handler with the three values it returns. The signature is "
        "verified, then the outcome is read from Razorpay itself (never trusted from the browser). "
        "Returns the same body as `GET /payments/{reference}`.",
        request=VerifyCheckoutSerializer,
        responses={
            200: PaymentStatusSerializer,
            400: VALIDATION_ERROR,
            401: UNAUTHORIZED,
            404: NOT_FOUND,
        },
    )
    def post(self, request):
        data = VerifyCheckoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        payment = services.verify_checkout(request.user, **data.validated_data)
        return Response(_status_body(request, payment))


class WebhookView(APIView):
    """Razorpay -> us. Authenticated by the HMAC signature of the raw body, not by a user token."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Payments"],
        summary="Razorpay webhook",
        description="Handles `payment_link.paid|cancelled|expired` (Payment Link flow) and "
        "`payment.authorized`, `payment.captured`, `order.paid` (Checkout flow). "
        "Verifies `X-Razorpay-Signature` against the raw body; replays are no-ops.",
        request=None,
        responses={200: WebhookResultSerializer, 400: BAD_WEBHOOK},
    )
    def post(self, request):
        body = request.body  # raw bytes: the signature covers exactly these, so read them before parsing
        if not verify_webhook_signature(body, request.headers.get("X-Razorpay-Signature", "")):
            return Response({"message": "Invalid signature."}, status=400)
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
            event_id = request.headers.get("X-Razorpay-Event-Id") or hashlib.sha256(body).hexdigest()
            result = services.handle_webhook(event_id, payload)
        except (ValueError, TypeError, AttributeError):
            logger.exception("Malformed payment webhook")
            return Response({"message": "Malformed webhook."}, status=400)
        return Response({"result": result})
