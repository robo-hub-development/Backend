from rest_framework import serializers

from apps.accounts.models import OtpChallenge
from apps.accounts.validators import validate_mobile


class OtpSendSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=OtpChallenge.Purpose.choices)
    email = serializers.EmailField(max_length=254)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    mobile = serializers.CharField(
        max_length=16, required=False, allow_blank=True, validators=[validate_mobile]
    )

    def validate(self, attrs):
        if attrs["purpose"] == OtpChallenge.Purpose.REGISTER:
            errors = {
                f: "This field is required." for f in ("name", "mobile") if not attrs.get(f, "").strip()
            }
            if errors:
                raise serializers.ValidationError(errors)
            attrs["name"] = attrs["name"].strip()
            attrs["mobile"] = attrs["mobile"].strip()
        else:
            attrs["name"] = attrs["mobile"] = ""  # ignored for login
        return attrs


class OtpVerifySerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=OtpChallenge.Purpose.choices)
    email = serializers.EmailField(max_length=254)
    otp = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter the 6-digit code."})


class UserSerializer(serializers.Serializer):
    id = serializers.SerializerMethodField()
    name = serializers.CharField()
    email = serializers.EmailField()
    mobile = serializers.CharField()

    def get_id(self, obj) -> str:
        return str(obj.pk)


class SavedAddressSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    fullName = serializers.CharField(source="full_name", max_length=120)
    mobile = serializers.CharField(max_length=16, validators=[validate_mobile])
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100)
    pincode = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Enter a valid 6-digit pincode."})
    isDefault = serializers.BooleanField(
        source="is_default",
        required=False,
        help_text="Make this the default address. The first saved address is always the default.",
    )
