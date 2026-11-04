from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.api_docs import UNAUTHORIZED, VALIDATION_ERROR, error_response
from apps.common.throttling import OrderPlaceThrottle
from apps.orders import selectors, services
from apps.orders.models import Order
from apps.payments import services as payment_services
from apps.payments.api.serializers import RedirectSerializer

from .serializers import CreateOrderSerializer, OrderSerializer, PlacedOrderSerializer

NOT_FOUND = error_response("No such order for the signed-in user.")


def _order_data(request, order):
    return OrderSerializer(order, context={"request": request}).data


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
        responses={200: OrderSerializer(many=True), 401: UNAUTHORIZED},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=["Orders"],
        summary="Place an order (COD) or start an online payment",
        description="The order is built from the cart lines listed in `cartItems` (live lines of the cart "
        "named by `cartId`); quantities and prices come from the server. "
        "Lines not listed stay in the cart.\n\n"
        "- `cod`: allowed only for exactly one line with quantity 1; "
        "creates the order now (**201**, `order`).\n"
        "- `online`: no order yet. Stock is held, a Razorpay payment link is created and its URL is returned "
        "(**200**, `redirectUrl`). The order is created when the payment succeeds; poll "
        "`GET /payments/{reference}` from the return page. **503** when online payments are not configured.",
        request=CreateOrderSerializer,
        responses={
            201: PlacedOrderSerializer,
            200: RedirectSerializer,
            400: VALIDATION_ERROR,
            401: UNAUTHORIZED,
            502: error_response("The payment gateway failed; nothing was held."),
            503: error_response("Online payments are not configured."),
        },
    )
    def post(self, request):
        data = CreateOrderSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        args = {
            "cart_id": data.validated_data["cart_id"],
            "cart_item_ids": data.validated_data["cart_item_ids"],
            "address": data.validated_data["address"],
        }
        if data.validated_data["payment_method"] == Order.PaymentMethod.ONLINE:
            attempt = payment_services.start_online_payment(request.user, **args)
            return Response({"redirectUrl": attempt.short_url})
        order = services.place_order(request.user, **args)
        order = selectors.get_user_order(request.user, order.pk)
        return Response({"order": _order_data(request, order)}, status=status.HTTP_201_CREATED)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders"],
        summary="Order detail",
        responses={200: OrderSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def get(self, request, pk):
        return Response(_order_data(request, selectors.get_user_order(request.user, pk)))


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Orders"],
        summary="Cancel an order",
        description="Allowed while the order is pending or confirmed and inside the cancellation window; "
        "stock is returned. `canCancel` on the order tells the client whether to offer this.",
        request=None,
        responses={200: OrderSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def post(self, request, pk):
        services.cancel_order(request.user, pk)
        return Response(_order_data(request, selectors.get_user_order(request.user, pk)))
