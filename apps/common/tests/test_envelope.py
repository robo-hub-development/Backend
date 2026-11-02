import pytest

from apps.accounts.models import User
from apps.accounts.tests.conftest import SEND, VERIFY, last_code

pytestmark = pytest.mark.django_db


def test_success_is_wrapped(client):
    body = client.get("/api/v1/categories").json()
    assert body == {"success": True, "data": [], "error": None}


def test_paginated_list_keeps_its_envelope_inside_data(client):
    body = client.get("/api/v1/products").json()
    assert body["success"] is True and body["error"] is None
    assert set(body["data"]) == {"items", "page", "pageSize", "total", "totalPages"}


def test_validation_error_is_wrapped(client):
    body = client.post(SEND, {"purpose": "login", "email": "bad"}, content_type="application/json").json()
    assert body["success"] is False and body["data"] is None
    assert body["error"]["message"]
    assert "email" in body["error"]["errors"]


def test_not_found_and_unauthorised_are_wrapped(client):
    missing = client.get("/api/v1/products/nope")
    assert missing.status_code == 404
    assert missing.json() == {"success": False, "data": None, "error": {"message": "Not found."}}
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert me.json()["success"] is False and me.json()["error"]["message"]


def test_status_codes_are_unchanged(client):
    assert client.get("/api/v1/categories").status_code == 200
    assert client.get("/api/v1/products", {"sort": "bogus"}).status_code == 400


def test_full_login_flow_and_204_logout_stay_empty(client):
    User.objects.create_user(email="env@example.com", name="E", mobile="9000012345")
    client.post(SEND, {"purpose": "login", "email": "env@example.com"}, content_type="application/json")
    verified = client.post(
        VERIFY,
        {"purpose": "login", "email": "env@example.com", "otp": last_code()},
        content_type="application/json",
    ).json()
    assert verified["success"] is True and verified["data"]["token"] and verified["error"] is None
    logout = client.post("/api/v1/auth/logout", HTTP_AUTHORIZATION=f"Bearer {verified['data']['token']}")
    assert logout.status_code == 204 and logout.content == b""


def test_health_probe_is_not_wrapped(client):
    assert client.get("/health/").json() == {"status": "ok"}


def test_openapi_documents_the_envelope(client):
    import yaml

    schema = yaml.safe_load(client.get("/api/v1/schema/").content)
    ok = schema["paths"]["/api/v1/auth/me"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert set(ok["properties"]) == {"success", "data", "error"} and ok["properties"]["success"]["enum"] == [
        True
    ]
    bad = schema["paths"]["/api/v1/auth/me"]["get"]["responses"]["401"]["content"]["application/json"][
        "schema"
    ]
    assert bad["properties"]["success"]["enum"] == [False]
    assert bad["properties"]["error"] == {"$ref": "#/components/schemas/Error"}
    assert "message" in schema["components"]["schemas"]["Error"]["properties"]
    assert "204" in schema["paths"]["/api/v1/auth/logout"]["post"]["responses"]
