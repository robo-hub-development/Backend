"""OpenAPI helpers shared by every app: error schemas and the bearer auth scheme."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "apps.accounts.authentication.BearerTokenAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT returned by `POST /auth/otp/verify`.",
        }


class ErrorSerializer(serializers.Serializer):
    message = serializers.CharField()
    errors = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False)


def error_response(description: str) -> OpenApiResponse:
    return OpenApiResponse(response=ErrorSerializer, description=description)


VALIDATION_ERROR = error_response("Validation failed; `errors` maps field -> messages.")
UNAUTHORIZED = error_response("Missing, invalid, expired or revoked token.")
THROTTLED = error_response("Too many requests; see the `Retry-After` header.")


def envelope(name: str, **fields):
    """Inline serializer for small response bodies."""
    return inline_serializer(name=name, fields=fields)


def wrap_envelope(result, generator, request, public):
    """OpenAPI hook: describe every JSON response inside the {success, data, error} envelope."""
    for operations in result.get("paths", {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for code, response in operation.get("responses", {}).items():
                content = response.get("content", {}).get("application/json")
                if not content or "schema" not in content:
                    continue
                inner = content["schema"]
                if code.startswith("2"):
                    content["schema"] = {
                        "type": "object",
                        "required": ["success", "data", "error"],
                        "properties": {
                            "success": {"type": "boolean", "enum": [True]},
                            "data": inner,
                            "error": {"nullable": True, "description": "Always null on success."},
                        },
                    }
                else:
                    content["schema"] = {
                        "type": "object",
                        "required": ["success", "data", "error"],
                        "properties": {
                            "success": {"type": "boolean", "enum": [False]},
                            "data": {"nullable": True, "description": "Always null on failure."},
                            "error": inner,
                        },
                    }
    return result
