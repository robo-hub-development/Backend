from rest_framework import status
from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    """Wrap every API body in {"success", "data", "error"}.

    Views and the exception handler keep returning plain data / {"message", "errors"}; the shape is applied
    in one place, at render time. 204 responses stay empty.
        success: {"success": true,  "data": <payload>, "error": null}
        failure: {"success": false, "data": null,      "error": {"message": str, "errors": {...}?}}
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = (renderer_context or {}).get("response")
        if response is None:
            return super().render(data, accepted_media_type, renderer_context)
        if response.status_code == status.HTTP_204_NO_CONTENT:
            return b""
        if status.is_success(response.status_code):
            body = {"success": True, "data": data, "error": None}
        else:
            body = {"success": False, "data": None, "error": data}
        return super().render(body, accepted_media_type, renderer_context)
