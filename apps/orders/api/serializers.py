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


class OrderSerializer(serializers.Serializer):
    id = serializers.SerializerMethodField()
    orderNumber = serializers.CharField(source="order_number")
    status = serializers.CharField()
    paymentMethod = serializers.CharField(source="payment_method")
    paymentStatus = serializers.CharField(source="payment_status")
    items = OrderItemSerializer(many=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    placedAt = serializers.DateTimeField(source="created_at")
    canCancel = serializers.BooleanField(source="can_cancel")

    def get_id(self, obj) -> str:
        return str(obj.pk)


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

    def validate_cartItems(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each cart item may appear only once.")
        return value


class PlacedOrderSerializer(serializers.Serializer):
    order = OrderSerializer()
