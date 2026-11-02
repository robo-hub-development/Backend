import pytest
import yaml
from django.conf import settings

pytestmark = pytest.mark.skipif(not settings.API_DOCS_ENABLED, reason="docs disabled")

EXPECTED = {
    ("/api/v1/health", "get"),
    ("/api/v1/auth/otp/send", "post"),
    ("/api/v1/auth/otp/verify", "post"),
    ("/api/v1/auth/me", "get"),
    ("/api/v1/auth/logout", "post"),
    ("/api/v1/categories", "get"),
    ("/api/v1/categories/{slug}", "get"),
    ("/api/v1/products", "get"),
    ("/api/v1/products/filters", "get"),
    ("/api/v1/products/{slug}", "get"),
    ("/api/v1/products/{slug}/related", "get"),
    ("/api/v1/search/suggestions", "get"),
}


def _schema(client):
    response = client.get("/api/v1/schema/")
    assert response.status_code == 200
    return yaml.safe_load(response.content)


def test_schema_lists_every_v1_route(client):
    schema = _schema(client)
    found = {
        (p, m) for p, ops in schema["paths"].items() for m in ops if m in {"get", "post", "put", "delete"}
    }
    assert EXPECTED <= found


def test_bearer_scheme_and_secured_routes(client):
    schema = _schema(client)
    assert schema["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/auth/me"]["get"]["security"] == [{"bearerAuth": []}]
    assert schema["paths"]["/api/v1/auth/otp/send"]["post"].get("security") in (None, [], [{}])


def test_error_responses_documented(client):
    responses = _schema(client)["paths"]["/api/v1/auth/otp/verify"]["post"]["responses"]
    assert {"200", "400", "429"} <= set(responses)


def test_swagger_ui_and_redoc_served(client):
    for url in ("/api/docs/", "/api/v1/docs/", "/api/v1/redoc/"):
        assert client.get(url).status_code == 200
