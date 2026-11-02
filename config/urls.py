from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.common.health import health
from config.api.versions import API_VERSIONS, DEFAULT_API_VERSION

# One namespaced include per version -> /api/<version>/..., read by DRF NamespaceVersioning.
api_patterns = [
    path(f"api/{version}/", include((module, version), namespace=version))
    for version, module in API_VERSIONS.items()
]

# Documentation: per-version OpenAPI schema + Swagger UI / ReDoc, plus /api/docs/ for the default version.
# Gated by API_DOCS_ENABLED so production does not expose it unless explicitly turned on.
docs_patterns = []
if settings.API_DOCS_ENABLED:
    for version in API_VERSIONS:
        docs_patterns += [
            path(
                f"api/{version}/schema/",
                SpectacularAPIView.as_view(api_version=version),
                name=f"schema-{version}",
            ),
            path(
                f"api/{version}/docs/",
                SpectacularSwaggerView.as_view(url_name=f"schema-{version}"),
                name=f"swagger-{version}",
            ),
            path(
                f"api/{version}/redoc/",
                SpectacularRedocView.as_view(url_name=f"schema-{version}"),
                name=f"redoc-{version}",
            ),
        ]
    docs_patterns += [
        path(
            "api/docs/",
            SpectacularSwaggerView.as_view(url_name=f"schema-{DEFAULT_API_VERSION}"),
            name="swagger-default",
        ),
    ]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    *docs_patterns,
    *api_patterns,
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
