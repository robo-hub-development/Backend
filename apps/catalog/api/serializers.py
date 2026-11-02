from decimal import Decimal

from rest_framework import serializers

from apps.catalog.selectors import DEFAULT_SORT, SORTS


def _money(**kwargs):
    # Prices are JSON numbers (INR) per the storefront contract, not strings.
    return serializers.DecimalField(max_digits=10, decimal_places=2, coerce_to_string=False, **kwargs)


class ProductQuerySerializer(serializers.Serializer):
    """Query string of GET /products (page/pageSize are handled by the paginator)."""

    category = serializers.SlugField(required=False)
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
    sort = serializers.ChoiceField(choices=list(SORTS), required=False, default=DEFAULT_SORT)
    minPrice = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal(0), required=False)
    maxPrice = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal(0), required=False)

    def validate(self, attrs):
        low, high = attrs.get("minPrice"), attrs.get("maxPrice")
        if low is not None and high is not None and low > high:
            raise serializers.ValidationError({"minPrice": "Must not exceed maxPrice."})
        return attrs

    def selector_kwargs(self) -> dict:
        data = self.validated_data
        return {
            "category": data.get("category"),
            "q": data.get("q"),
            "min_price": data.get("minPrice"),
            "max_price": data.get("maxPrice"),
            "sort": data["sort"],
        }


class SuggestionQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)


def _media_of(product, media_type: str) -> list:
    """Prefetched media of one type, already ordered by position (no extra queries)."""
    return [m for m in product.media.all() if m.media_type == media_type]


def _first_image_url(product, request) -> str:
    images = _media_of(product, "image")  # videos never act as the thumbnail
    return request.build_absolute_uri(images[0].file.url) if images else ""


class _OmitNullMixin:
    omit_if_null: tuple[str, ...] = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for key in self.omit_if_null:
            if data.get(key) is None:
                data.pop(key, None)
        return data


class ProductSerializer(_OmitNullMixin, serializers.Serializer):
    omit_if_null = ("compareAtPrice",)

    id = serializers.SerializerMethodField()
    slug = serializers.CharField()
    name = serializers.CharField()
    summary = serializers.CharField()
    categorySlug = serializers.SerializerMethodField()
    categoryName = serializers.SerializerMethodField()
    price = _money()
    compareAtPrice = _money(source="compare_at_price", allow_null=True)
    image = serializers.SerializerMethodField()
    rating = serializers.FloatField()
    reviewCount = serializers.IntegerField(source="review_count")
    inStock = serializers.BooleanField(source="in_stock")
    isNew = serializers.BooleanField(source="is_new")
    soldCount = serializers.IntegerField(source="sold_count")

    def get_id(self, obj) -> str:
        return str(obj.pk)

    def get_categorySlug(self, obj) -> str:
        return obj.category.top_level.slug

    def get_categoryName(self, obj) -> str:
        return obj.category.top_level.name

    def get_image(self, obj) -> str:
        return _first_image_url(obj, self.context["request"])


class ProductMediaSerializer(serializers.Serializer):
    url = serializers.SerializerMethodField()
    alt = serializers.CharField()

    def get_url(self, obj) -> str:
        return self.context["request"].build_absolute_uri(obj.file.url)


class ProductVariantSerializer(_OmitNullMixin, serializers.Serializer):
    omit_if_null = ("compareAtPrice",)

    id = serializers.SerializerMethodField()
    label = serializers.CharField(source="option.title")
    slug = serializers.CharField(source="option.slug")
    type = serializers.CharField(source="option.type")  # "size" | "color"
    price = _money()
    compareAtPrice = _money(source="compare_at_price", allow_null=True)
    inStock = serializers.BooleanField(source="in_stock")
    imageIndex = serializers.IntegerField(source="image_index")

    def get_id(self, obj) -> str:
        return str(obj.pk)


class ProductDetailSerializer(ProductSerializer):
    description = serializers.CharField()
    images = serializers.SerializerMethodField()
    videos = serializers.SerializerMethodField()
    variantLabel = serializers.CharField(source="variant_label")
    variants = ProductVariantSerializer(many=True)

    def get_images(self, obj) -> list:
        return ProductMediaSerializer(_media_of(obj, "image"), many=True, context=self.context).data

    def get_videos(self, obj) -> list:
        return ProductMediaSerializer(_media_of(obj, "video"), many=True, context=self.context).data


class ProductSuggestionSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    categoryName = serializers.SerializerMethodField()
    price = _money()
    image = serializers.SerializerMethodField()

    def get_categoryName(self, obj) -> str:
        return obj.category.top_level.name

    def get_image(self, obj) -> str:
        return _first_image_url(obj, self.context["request"])


class PriceRangeSerializer(serializers.Serializer):
    min = _money()
    max = _money()


class ProductFiltersSerializer(serializers.Serializer):
    priceRange = PriceRangeSerializer()


class CategoryChildSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()


class CategoryParentSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()


class CategorySerializer(_OmitNullMixin, serializers.Serializer):
    omit_if_null = ("parent",)

    slug = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    image = serializers.SerializerMethodField()
    productCount = serializers.IntegerField(source="product_count")
    children = CategoryChildSerializer(many=True, source="active_children")
    parent = CategoryParentSerializer(allow_null=True)

    def get_image(self, obj) -> str:
        url = (
            obj.image.url
            if obj.image
            else (obj.parent.image.url if obj.parent_id and obj.parent.image else "")
        )
        return self.context["request"].build_absolute_uri(url) if url else ""
