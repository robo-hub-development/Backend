from unittest import mock

import pytest

pytestmark = pytest.mark.django_db
URL = "/api/v1/health"


def test_api_health_ok_in_envelope(client):
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {"status": "ok", "checks": {"database": "ok"}},
        "error": None,
    }


def test_api_health_needs_no_auth_even_with_a_bad_token(client):
    assert client.get(URL, HTTP_AUTHORIZATION="Bearer garbage").status_code == 200


def test_api_health_reports_503_when_database_is_down(client):
    with mock.patch("apps.common.views.check_database", return_value=False):
        response = client.get(URL)
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False and body["data"] is None
    assert body["error"]["errors"] == {"database": ["unreachable"]}


def test_plain_probe_still_available_and_unwrapped(client):
    assert client.get("/health/").json() == {"status": "ok"}
    with mock.patch("apps.common.health.check_database", return_value=False):
        down = client.get("/health/")
    assert down.status_code == 503 and down.json() == {"status": "unavailable"}


def test_check_database_never_raises():
    from apps.common.health import check_database

    with mock.patch("apps.common.health.connection.cursor", side_effect=RuntimeError("boom")):
        assert check_database() is False


def test_plain_probe_works_without_trailing_slash_and_without_redirect(client):
    response = client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_production_trusts_the_render_hostname(monkeypatch):
    import importlib

    for key, value in {
        "DJANGO_SECRET_KEY": "x" * 60,
        "DATABASE_URL": "postgres://u:p@localhost:5432/db",
        "RENDER_EXTERNAL_HOSTNAME": "backend-w586.onrender.com",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DJANGO_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    production = importlib.reload(importlib.import_module("config.settings.production"))
    assert "backend-w586.onrender.com" in production.ALLOWED_HOSTS
    assert "https://backend-w586.onrender.com" in production.CSRF_TRUSTED_ORIGINS
