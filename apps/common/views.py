from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_docs import envelope, error_response
from .health import check_database


class HealthView(APIView):
    """GET /api/v1/health: liveness plus dependency checks, in the standard response envelope."""

    authentication_classes: list = []  # reachable by monitors even with a stale/invalid token
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["System"],
        summary="Health check",
        description="200 when the API and database are reachable; otherwise 503 with the failing checks.",
        responses={
            200: envelope(
                "HealthResponse",
                status=serializers.CharField(),
                checks=serializers.DictField(child=serializers.CharField()),
            ),
            503: error_response("A dependency is unavailable; `error.errors` names the failing checks."),
        },
    )
    def get(self, request):
        database_ok = check_database()
        if database_ok:
            return Response({"status": "ok", "checks": {"database": "ok"}})
        return Response(
            {"message": "Service unavailable.", "errors": {"database": ["unreachable"]}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
