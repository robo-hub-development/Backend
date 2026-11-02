from django.db import connection
from django.http import JsonResponse


def health(request):
    """Liveness + DB readiness probe for load balancers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - probe must never raise
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
