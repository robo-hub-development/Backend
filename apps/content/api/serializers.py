from rest_framework import serializers


class CtaSerializer(serializers.Serializer):
    label = serializers.CharField()
    href = serializers.CharField()


class BannerSerializer(serializers.Serializer):
    id = serializers.SerializerMethodField()
    eyebrow = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    image = serializers.SerializerMethodField()
    imageAlt = serializers.CharField(source="image_alt")
    primaryCta = CtaSerializer(source="primary_cta")
    secondaryCta = CtaSerializer(source="secondary_cta", required=False)

    def get_id(self, obj) -> str:
        return str(obj.pk)

    def get_image(self, obj) -> str:
        return self.context["request"].build_absolute_uri(obj.image.url)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not instance.secondary_cta:  # optional in the contract: omitted rather than null
            data.pop("secondaryCta", None)
        return data


class ArticleSerializer(serializers.Serializer):
    """Card fields, as the storefront's blog list and home page use them."""

    slug = serializers.CharField()
    title = serializers.CharField()
    excerpt = serializers.CharField()
    image = serializers.SerializerMethodField(
        help_text="The media URL when it is an image, else empty (kept for existing clients; prefer `media`)."
    )
    media = serializers.SerializerMethodField(help_text="URL of the main image or video.")
    mediaType = serializers.CharField(source="media_type", help_text="`image` or `video`.")
    category = serializers.SerializerMethodField(help_text="Category name, empty when the article has none.")
    categorySlug = serializers.SerializerMethodField(
        help_text="Category slug for `?category=`, empty if none."
    )
    author = serializers.CharField()
    publishedAt = serializers.DateTimeField(source="published_at")
    readMinutes = serializers.IntegerField(source="read_minutes")

    def _media_url(self, obj) -> str:
        return self.context["request"].build_absolute_uri(obj.media.url)

    def get_media(self, obj) -> str:
        return self._media_url(obj)

    def get_image(self, obj) -> str:
        return self._media_url(obj) if obj.media_type == "image" else ""

    def get_category(self, obj) -> str:
        return obj.category.name if obj.category else ""

    def get_categorySlug(self, obj) -> str:  # noqa: N802 - the name is the JSON field the storefront reads
        return obj.category.slug if obj.category else ""


class ArticleDetailSerializer(ArticleSerializer):
    content = serializers.CharField(source="body", help_text="The article body as sanitized HTML.")


class ArticleQuerySerializer(serializers.Serializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=100)
    category = serializers.SlugField(required=False, allow_blank=True, default="")


class ArticleCategorySerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    articleCount = serializers.IntegerField(source="article_count")
