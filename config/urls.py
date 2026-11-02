from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.common.health import health
from config.api.versions import API_VERSIONS

# One namespaced include per version -> /api/<version>/..., read by DRF NamespaceVersioning.
api_patterns = [
    path(f"api/{version}/", include((module, version), namespace=version))
    for version, module in API_VERSIONS.items()
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    *api_patterns,
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
