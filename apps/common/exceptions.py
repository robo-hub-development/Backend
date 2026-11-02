"""Maps every API error to the storefront contract: {"message": str, "errors": {field: str | [str]}}."""

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _flatten(detail):
    """Turn DRF error details into {field: [str]}; non-dict errors are keyed '_'."""
    if isinstance(detail, dict):
        result = {}
        for key, value in detail.items():
            if isinstance(value, dict):
                result[key] = _flatten(value)
            elif isinstance(value, list):
                result[key] = [str(item) for item in value]
            else:
                result[key] = [str(value)]
        return result
    if isinstance(detail, list):
        return {"_": [str(item) for item in detail]}
    return {"_": [str(detail)]}


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        # Unhandled error: log with traceback, return a safe generic message.
        logger.exception("Unhandled API error", exc_info=exc)
        return Response({"message": "Something went wrong."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    data = response.data
    if response.status_code == status.HTTP_400_BAD_REQUEST and isinstance(data, (dict, list)):
        errors = _flatten(data)
        message = "Please correct the highlighted fields."
        if "detail" in errors:
            message = errors.pop("detail")[0]
        response.data = {"message": message, "errors": errors} if errors else {"message": message}
    else:
        detail = data.get("detail") if isinstance(data, dict) else None
        response.data = {"message": str(detail) if detail else "Request failed."}
    return response
