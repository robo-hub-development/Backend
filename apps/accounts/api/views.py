from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.common.api_docs import THROTTLED, UNAUTHORIZED, VALIDATION_ERROR, envelope
from apps.common.throttling import OtpSendThrottle, OtpVerifyThrottle

from .serializers import OtpSendSerializer, OtpVerifySerializer, UserSerializer


class OtpSendView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpSendThrottle]

    @extend_schema(
        tags=["Auth"],
        summary="Send a login / register OTP",
        description="`login` needs an existing account; `register` needs `name` and a `mobile` and email "
        "that are not already registered (both must be unique).",
        request=OtpSendSerializer,
        responses={
            200: envelope("OtpSendResponse", expiresInSeconds=serializers.IntegerField()),
            400: VALIDATION_ERROR,
            429: THROTTLED,
        },
    )
    def post(self, request):
        data = OtpSendSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        expires_in = services.send_otp(**data.validated_data)
        return Response({"expiresInSeconds": expires_in})


class OtpVerifyView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [OtpVerifyThrottle]

    @extend_schema(
        tags=["Auth"],
        summary="Verify OTP and start a session",
        description="For `register` the account is created from the data sent to `/otp/send`.",
        request=OtpVerifySerializer,
        responses={
            200: envelope(
                "OtpVerifyResponse",
                user=UserSerializer(),
                token=serializers.CharField(),
                expiresInSeconds=serializers.IntegerField(),
            ),
            400: VALIDATION_ERROR,
            429: THROTTLED,
        },
    )
    def post(self, request):
        data = OtpVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user, token, ttl = services.verify_otp(**data.validated_data)
        return Response({"user": UserSerializer(user).data, "token": token, "expiresInSeconds": ttl})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="Current user",
        responses={200: envelope("MeResponse", user=UserSerializer()), 401: UNAUTHORIZED},
    )
    def get(self, request):
        return Response({"user": UserSerializer(request.user).data})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="End the current session",
        request=None,
        responses={204: None, 401: UNAUTHORIZED},
    )
    def post(self, request):
        services.revoke_token(request.auth)
        return Response(status=status.HTTP_204_NO_CONTENT)
