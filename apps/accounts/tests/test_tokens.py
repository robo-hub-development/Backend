from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AuthToken, User

from .conftest import login_token, register

pytestmark = pytest.mark.django_db
ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"


def authed(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_me_requires_token(api):
    response = api.get(ME)
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert set(response.data) == {"message"}


def test_me_returns_user(api, user):
    response = authed(login_token(api, user.email)).get(ME)
    assert response.status_code == 200
    assert response.data["user"]["email"] == user.email


@pytest.mark.parametrize("header", ["Bearer nonsense", "Bearer", "Bearer a b", "Token abc"])
def test_malformed_or_unknown_token_is_401_or_anonymous(api, header):
    api.credentials(HTTP_AUTHORIZATION=header)
    assert api.get(ME).status_code == 401


def test_expired_token_rejected(api, user):
    token = login_token(api, user.email)
    AuthToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert authed(token).get(ME).status_code == 401


def test_logout_revokes_token_and_returns_204(api, user):
    token = login_token(api, user.email)
    client = authed(token)
    assert client.post(LOGOUT).status_code == 204
    assert AuthToken.objects.get().revoked_at is not None
    assert client.get(ME).status_code == 401
    assert client.post(LOGOUT).status_code == 401


def test_logout_only_revokes_current_session(api, user):
    first, second = login_token(api, user.email), login_token(api, user.email)
    authed(first).post(LOGOUT)
    assert authed(second).get(ME).status_code == 200


def test_deactivated_user_token_rejected(api, user):
    token = login_token(api, user.email)
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert authed(token).get(ME).status_code == 401


def test_register_then_use_token(api):
    token = register(api).data["token"]
    assert authed(token).get(ME).data["user"]["email"] == "new@example.com"


def test_customers_cannot_use_admin_login(db, client):
    User.objects.create_user(email="c@example.com", name="C", mobile="9000000012")
    assert not client.login(username="c@example.com", password="anything")
