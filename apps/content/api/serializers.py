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
