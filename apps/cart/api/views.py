from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cart import services
from apps.common.api_docs import UNAUTHORIZED, VALIDATION_ERROR

from .serializers import CartInputSerializer, CartSerializer


def _cart_response(request, lines):
    return Response(CartSerializer({"items": lines}, context={"request": request}).data)


class CartView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Cart"],
        summary="Current cart, priced from the catalogue",
        responses={200: CartSerializer, 401: UNAUTHORIZED},
    )
    def get(self, request):
        return _cart_response(request, services.get_cart(request.user))

    @extend_schema(
        tags=["Cart"],
        summary="Replace the whole cart",
        description="Max 10 per product. Each product at most once. Send `items: []` to empty the cart.",
        request=CartInputSerializer,
        responses={200: CartSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED},
    )
    def put(self, request):
        data = CartInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _cart_response(request, services.replace_cart(request.user, data.validated_data["items"]))
