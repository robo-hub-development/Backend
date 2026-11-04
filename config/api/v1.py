"""Routes for API v1. Each feature app adds one entry."""

from django.urls import include, path

from apps.common.views import HealthView

urlpatterns = [
    path("health", HealthView.as_view(), name="api-health"),
    path("auth/", include("apps.accounts.api.urls")),
    path("", include("apps.catalog.api.urls")),
    path("", include("apps.cart.api.urls")),
    path("", include("apps.orders.api.urls")),
    path("", include("apps.payments.api.urls")),
    path("", include("apps.content.api.urls")),
]
