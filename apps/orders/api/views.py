from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api_docs import UNAUTHORIZED, VALIDATION_ERROR, error_response
from apps.common.throttling import OrderPlaceThrottle
from apps.orders import checkout, selectors
from apps.payments import gateway as payment_gateway
from apps.payments import services as payment_services
from apps.payments.api.serializers import PaymentStartSerializer
from apps.payments.models import Payment

from .serializers import (
    CreateOrderSerializer,
    OrderSerializer,
    PayOrderSerializer,
    QuoteRequestSerializer,
    QuoteSerializer,
)

NOT_FOUND = error_response("No such order for the signed-in user.")
PAYMENT_ERRORS = {
    502: error_response("The payment gateway failed."),
    503: error_response("Online payments are not configured."),
}


def _order_data(request, order):
    return OrderSerializer(order, context={"request": request}).data


def _payment_start(payment: Payment | None) -> dict:
    """What the storefront needs to take the shopper through the online payment (empty when none is due)."""
    if payment is None:
        return {}
    if payment.flow == Payment.Flow.CHECKOUT:
        return {
            "reference": payment.reference,
            "razorpay": {
                "keyId": payment_gateway.get_gateway().public_key,
                "orderId": payment.gateway_order_id,
                "amount": payment_services.to_paise(payment.amount),
                "currency": payment.currency,
            },
        }
    return {"reference": payment.reference, "redirectUrl": payment.short_url}


class OrderListCreateView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrderSerializer

    def get_queryset(self):
        return selectors.user_orders(self.request.user)

    def get_throttles(self):
        return [OrderPlaceThrottle()] if self.request.method == "POST" else []

    @extend_schema(
        tags=["Orders"],
        summary="Order history, newest first",
        description="Includes orders that are still `awaiting_payment` (they carry `payment` to resume).",
        responses={200: OrderSerializer(many=True), 401: UNAUTHORIZED},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["Orders"],
        summary="Place an order",
        description="The order is built from the cart lines listed in `cartItems` (live lines of the cart "
        "named by `cartId`); quantities and prices come from the server. "
        "Lines not listed stay in the cart.\n\n"
        "The order is created immediately and returned (**201**).\n"
        "- `online`: the whole price is due online. The order is `awaiting_payment`; "
        "the response also carries `reference` and either `redirectUrl` (paymentFlow `link`) "
        "or `razorpay` (paymentFlow `checkout`).\n"
        "- `cod`: allowed only for exactly one line with quantity 1. The item price is paid in cash on "
        "delivery; a transit fee (from the COD policy table) is paid online first. With a fee the order is "
        "`awaiting_payment` and the response carries the same payment parameters; "
        "with no fee it is `pending`.\n\n"
        "Poll `GET /payments/{reference}` after paying. **503** when an online payment is needed but not "
        "configured; **502** when the gateway failed (the order is cancelled and its stock released).",
        request=CreateOrderSerializer,
        responses={201: PaymentStartSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED, **PAYMENT_ERRORS},
    )
    def post(self, request):
        data = CreateOrderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        placed = checkout.place_order(
            request.user,
            cart_id=data.validated_data["cart_id"],
            cart_item_ids=data.validated_data["cart_item_ids"],
            address=data.validated_data["address"],
            payment_method=data.validated_data["payment_method"],
            flow=data.validated_data["payment_flow"],
        )
        order = selectors.get_user_order(request.user, placed.order.pk)
        body = {"order": _order_data(request, order), **_payment_start(placed.payment)}
        return Response(body, status=status.HTTP_201_CREATED)


class OrderQuoteView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders"],
        summary="Price an order before placing it",
        description="Same inputs as placing the order (only `state` and `pincode` of the address). "
        "Nothing is "
        "created or held. Shows the COD transit fee, what is payable online now and what is due on delivery.",
        request=QuoteRequestSerializer,
        responses={200: QuoteSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED},
    )
    def post(self, request):
        data = QuoteRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pricing = checkout.quote(
            request.user,
            data.validated_data["cart_id"],
            data.validated_data["cart_item_ids"],
            payment_method=data.validated_data["payment_method"],
            state=data.validated_data["address"]["state"],
            pincode=data.validated_data["address"]["pincode"],
        )
        return Response(
            QuoteSerializer(
                {
                    "subtotal": pricing.subtotal,
                    "charges": pricing.charges,
                    "total": pricing.total,
                    "payableNow": pricing.payable_now,
                    "dueOnDelivery": pricing.due_on_delivery,
                }
            ).data
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders"],
        summary="Order detail",
        responses={200: OrderSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def get(self, request, pk):
        return Response(_order_data(request, selectors.get_user_order(request.user, pk)))


class OrderPayView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [OrderPlaceThrottle]

    @extend_schema(
        tags=["Orders"],
        summary="Pay (or pay again for) an unpaid order",
        description="For an order that is `awaiting_payment`: returns the open payment, or starts a "
        "fresh one "
        "when the earlier link failed or expired. Returns the same payment parameters as placing the order.",
        request=PayOrderSerializer,
        responses={
            200: PaymentStartSerializer,
            400: VALIDATION_ERROR,
            401: UNAUTHORIZED,
            404: NOT_FOUND,
            **PAYMENT_ERRORS,
        },
    )
    def post(self, request, pk):
        data = PayOrderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        _, payment = checkout.pay_order(request.user, pk, flow=data.validated_data["payment_flow"])
        order = selectors.get_user_order(request.user, pk)
        return Response({"order": _order_data(request, order), **_payment_start(payment)})


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders"],
        summary="Cancel an order",
        description="Allowed while the order is `awaiting_payment`, `pending` or `confirmed` and inside the "
        "cancellation window; stock is returned. Anything paid online (full price, or the COD fee) is "
        "refunded because the order has not been dispatched. `canCancel` on the order tells the client "
        "whether to offer this.",
        request=None,
        responses={200: OrderSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def post(self, request, pk):
        checkout.cancel_order(request.user, pk)
        return Response(_order_data(request, selectors.get_user_order(request.user, pk)))
