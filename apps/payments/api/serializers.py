from rest_framework import serializers

from apps.orders.api.serializers import OrderSerializer


class CheckoutParamsSerializer(serializers.Serializer):
    keyId = serializers.CharField()
    orderId = serializers.CharField()
    amount = serializers.IntegerField(help_text="Amount in paise, as Checkout expects.")
    currency = serializers.CharField()


class PaymentStartSerializer(serializers.Serializer):
    """An order plus, when money is due online, how to pay (one of redirectUrl / razorpay)."""

    order = OrderSerializer()
    reference = serializers.CharField(required=False, help_text="Poll GET /payments/{reference} with this.")
    redirectUrl = serializers.CharField(required=False, help_text="Hosted payment page (paymentFlow link).")
    razorpay = CheckoutParamsSerializer(required=False)


class VerifyCheckoutSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=32)
    razorpayOrderId = serializers.CharField(source="order_id", max_length=64)
    razorpayPaymentId = serializers.CharField(source="payment_id", max_length=64)
    razorpaySignature = serializers.CharField(source="signature", max_length=128)


class PaymentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["success", "failed", "pending"])
    order = OrderSerializer(required=False)


class WebhookResultSerializer(serializers.Serializer):
    result = serializers.CharField()
