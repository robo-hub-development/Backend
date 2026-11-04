import hashlib
import json
import logging

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api_docs import UNAUTHORIZED, error_response
from apps.orders import selectors as order_selectors
from apps.orders.api.serializers import OrderSerializer
from apps.payments import services
from apps.payments.gateway import verify_webhook_signature
from apps.payments.models import PaymentAttempt

from .serializers import PaymentStatusSerializer, WebhookResultSerializer

logger = logging.getLogger(__name__)

NOT_FOUND = error_response("No such payment for the signed-in user.")
BAD_WEBHOOK = error_response("Missing/invalid signature or malformed body.")

# What the storefront's payment-return page understands.
STATUS_FOR_FRONTEND = {
    PaymentAttempt.Status.PENDING: "pending",
    PaymentAttempt.Status.PAID: "success",
    PaymentAttempt.Status.FAILED: "failed",
    PaymentAttempt.Status.NEEDS_REFUND: "failed",  # paid but unfulfillable; staff are refunding it
}


class PaymentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Payments"],
        summary="Outcome of an online payment",
        description="Called from the payment return page. While `pending` it asks the gateway directly, so "
        "a late webhook does not leave the shopper waiting. `order` is present once the order exists.",
        responses={200: PaymentStatusSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def get(self, request, reference):
        # Scoped to the caller: another user's reference is indistinguishable from an unknown one.
        attempt = PaymentAttempt.objects.filter(user=request.user, reference=reference).first()
        if attempt is None:
            raise NotFound
        attempt = services.reconcile(attempt)
        data = {"status": STATUS_FOR_FRONTEND[attempt.status]}
        if attempt.order_id:
            order = order_selectors.get_user_order(request.user, attempt.order_id)
            data["order"] = OrderSerializer(order, context={"request": request}).data
        return Response(data)


class WebhookView(APIView):
    """Razorpay -> us. Authenticated by the HMAC signature of the raw body, not by a user token."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Payments"],
        summary="Razorpay webhook",
        description="Handles `payment_link.paid`, `payment_link.cancelled` and `payment_link.expired`. "
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
