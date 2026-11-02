import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def check_database() -> bool:
    """True when the database answers a trivial query. Never raises (probes must not crash)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logger.exception("Health check: database unreachable")
        return False
    return True


def health(request):
    """Plain (unwrapped) probe for load balancers / orchestrators: GET /health/."""
    if check_database():
        return JsonResponse({"status": "ok"})
    return JsonResponse({"status": "unavailable"}, status=503)
