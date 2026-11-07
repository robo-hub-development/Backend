from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import address_services as services
from apps.common.api_docs import UNAUTHORIZED, VALIDATION_ERROR

from .serializers import SavedAddressSerializer

NOT_FOUND = {"description": "No such address for the signed-in user."}


class AddressListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Addresses"],
        summary="List my saved addresses",
        description="The default address comes first. At most 10 are kept, so the list is not paginated.",
        responses={200: SavedAddressSerializer(many=True), 401: UNAUTHORIZED},
    )
    def get(self, request):
        return Response(SavedAddressSerializer(services.list_addresses(request.user), many=True).data)

    @extend_schema(
        tags=["Addresses"],
        summary="Save an address",
        request=SavedAddressSerializer,
        responses={201: SavedAddressSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED},
    )
    def post(self, request):
        data = SavedAddressSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        address = services.create_address(request.user, **data.validated_data)
        return Response(SavedAddressSerializer(address).data, status=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Addresses"],
        summary="Get one saved address",
        responses={200: SavedAddressSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def get(self, request, pk):
        return Response(SavedAddressSerializer(services.get_address(request.user, pk)).data)

    @extend_schema(
        tags=["Addresses"],
        summary="Edit a saved address",
        description="Send only the fields to change. `isDefault: true` makes it the default address.",
        request=SavedAddressSerializer,
        responses={200: SavedAddressSerializer, 400: VALIDATION_ERROR, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def patch(self, request, pk):
        data = SavedAddressSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        address = services.update_address(request.user, pk, **data.validated_data)
        return Response(SavedAddressSerializer(address).data)

    @extend_schema(
        tags=["Addresses"],
        summary="Delete a saved address",
        description="Existing orders keep their own copy of the address.",
        responses={204: None, 401: UNAUTHORIZED, 404: NOT_FOUND},
    )
    def delete(self, request, pk):
        services.delete_address(request.user, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
