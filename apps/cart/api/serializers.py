from rest_framework import serializers

from apps.cart.models import MAX_QUANTITY_PER_PRODUCT

MAX_LINES = 50  # sanity bound on one request, not a business rule


class CartLineInputSerializer(serializers.Serializer):
    productId = serializers.UUIDField(source="product_id")
    quantity = serializers.IntegerField(min_value=1, max_value=MAX_QUANTITY_PER_PRODUCT)


class CartInputSerializer(serializers.Serializer):
    items = CartLineInputSerializer(many=True, max_length=MAX_LINES)


class PricedLineSerializer(serializers.Serializer):
    """Shared shape of a priced line in the cart (read from CartItem rows)."""

    id = serializers.UUIDField(help_text="Cart item id; send these as `cartItems` when ordering.")
    productId = serializers.SerializerMethodField()
    slug = serializers.CharField(source="product.slug")
    name = serializers.CharField(source="product.name")
    price = serializers.DecimalField(
        source="product.price", max_digits=10, decimal_places=2, coerce_to_string=False
    )
    image = serializers.SerializerMethodField()
    quantity = serializers.IntegerField()

    def get_productId(self, obj) -> str:
        return str(obj.product_id)

    def get_image(self, obj) -> str:
        # Prefetched media, already in position order; videos never act as the thumbnail.
        images = [m for m in obj.product.media.all() if m.media_type == "image"]
        return self.context["request"].build_absolute_uri(images[0].file.url) if images else ""


class CartSerializer(serializers.Serializer):
    id = serializers.UUIDField(
        allow_null=True, help_text="Cart id (null until the first PUT); send it as cartId when ordering."
    )
    items = PricedLineSerializer(many=True)
