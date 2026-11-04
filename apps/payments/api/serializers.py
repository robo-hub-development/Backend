from rest_framework import serializers

from apps.orders.api.serializers import OrderSerializer


class RedirectSerializer(serializers.Serializer):
    redirectUrl = serializers.URLField()


class PaymentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["success", "failed", "pending"])
    order = OrderSerializer(required=False)


class WebhookResultSerializer(serializers.Serializer):
    result = serializers.CharField()
