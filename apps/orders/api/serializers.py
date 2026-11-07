from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.validators import validate_mobile
from apps.orders.models import Order


class OrderItemSerializer(serializers.Serializer):
    productId = serializers.SerializerMethodField()
    slug = serializers.CharField(source="product_slug")
    name = serializers.CharField(source="product_name")
    price = serializers.DecimalField(
        source="unit_price", max_digits=10, decimal_places=2, coerce_to_string=False
    )
    image = serializers.SerializerMethodField()
    quantity = serializers.IntegerField()

    def get_productId(self, obj) -> str:
        return str(obj.product_id) if obj.product_id else ""

    def get_image(self, obj) -> str:
        # build_absolute_uri leaves already-absolute URLs (external storage) untouched.
        return self.context["request"].build_absolute_uri(obj.image) if obj.image else ""


def _money(**kwargs):
    return serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False, **kwargs)


class ChargeSerializer(serializers.Serializer):
    kind = serializers.CharField()
    label = serializers.CharField()
    amount = _money()


class PendingPaymentSerializer(serializers.Serializer):
    reference = serializers.CharField(help_text="Poll GET /payments/{reference}.")
    flow = serializers.CharField()
    amount = _money()
    redirectUrl = serializers.CharField(
        allow_null=True, help_text="Payment page (link flow); null for Checkout."
    )
    expiresAt = serializers.DateTimeField(allow_null=True)


class OrderSerializer(serializers.Serializer):
    id = serializers.SerializerMethodField()
    orderNumber = serializers.CharField(source="order_number")
    status = serializers.CharField()
    paymentMethod = serializers.CharField(source="payment_method")
    paymentStatus = serializers.CharField(source="payment_status")
    items = OrderItemSerializer(many=True)
    subtotal = _money()
    charges = ChargeSerializer(many=True)
    total = _money()
    amountPaid = _money(source="amount_paid")
    amountDue = _money(
        source="amount_due", help_text="Online money still due, or the cash due on delivery (COD)."
    )
    placedAt = serializers.DateTimeField(source="created_at")
    paymentDueBy = serializers.DateTimeField(source="payment_due_by", allow_null=True)
    canCancel = serializers.BooleanField(source="can_cancel")
    courierName = serializers.CharField(
        source="courier_name", help_text="Delivery partner; empty until the order is handed over."
    )
    trackingNumber = serializers.CharField(
        source="tracking_number",
        help_text="The delivery partner's tracking number; empty until the order is handed over.",
    )
    payment = serializers.SerializerMethodField(
        help_text="The online payment to complete, while one is open."
    )

    def get_id(self, obj) -> str:
        return str(obj.pk)

    @extend_schema_field(PendingPaymentSerializer(allow_null=True))
    def get_payment(self, obj):
        # payments are prefetched; "open" means a pending online payment that has not run out.
        now = timezone.now()
        for payment in obj.payments.all():
            if (
                payment.method == "online"
                and payment.status == "pending"
                and (payment.expires_at is None or payment.expires_at > now)
            ):
                return {
                    "reference": payment.reference,
                    "flow": payment.flow,
                    "amount": payment.amount,
                    "redirectUrl": payment.short_url or None,
                    "expiresAt": payment.expires_at,
                }
        return None


class AddressSerializer(serializers.Serializer):
    fullName = serializers.CharField(source="full_name", max_length=120)
    mobile = serializers.CharField(max_length=16, validators=[validate_mobile])
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    pincode = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter a valid 6-digit pincode."})


class CreateOrderSerializer(serializers.Serializer):
    cartId = serializers.UUIDField(source="cart_id", help_text="The caller's cart (id from GET /cart).")
    cartItems = serializers.ListField(
        child=serializers.UUIDField(),
        source="cart_item_ids",
        min_length=1,
        max_length=50,
        help_text="Ids of the cart lines to order (from GET /cart). Other lines stay in the cart.",
    )
    address = AddressSerializer()
    paymentMethod = serializers.ChoiceField(choices=Order.PaymentMethod.values, source="payment_method")
    paymentFlow = serializers.ChoiceField(
        choices=["link", "checkout"],
        source="payment_flow",
        default="checkout",
        help_text="How an online payment is taken (online orders, and the COD fee): `link` returns a hosted "
        "payment page URL, `checkout` returns the parameters for Razorpay Checkout.",
    )

    def validate_cartItems(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each cart item may appear only once.")
        return value


class PlacedOrderSerializer(serializers.Serializer):
    order = OrderSerializer()


class PayOrderSerializer(serializers.Serializer):
    paymentFlow = serializers.ChoiceField(
        choices=["link", "checkout"], source="payment_flow", default="checkout"
    )


class QuoteAddressSerializer(serializers.Serializer):
    state = serializers.CharField(max_length=100)
    pincode = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter a valid 6-digit pincode."})


class QuoteRequestSerializer(serializers.Serializer):
    cartId = serializers.UUIDField(source="cart_id")
    cartItems = serializers.ListField(
        child=serializers.UUIDField(), source="cart_item_ids", min_length=1, max_length=50
    )
    address = QuoteAddressSerializer()
    paymentMethod = serializers.ChoiceField(choices=Order.PaymentMethod.values, source="payment_method")


class QuoteSerializer(serializers.Serializer):
    subtotal = _money()
    charges = ChargeSerializer(many=True)
    total = _money()
    payableNow = _money(help_text="Online money due when the order is placed.")
    dueOnDelivery = _money(help_text="Cash for the courier (COD only).")
