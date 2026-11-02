import uuid
from datetime import timedelta

import jwt
import pytest
from django.conf import settings
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import AuthToken, User

pytestmark = pytest.mark.django_db
ME = "/api/v1/auth/me"


@pytest.fixture
def issued(user):
    token, ttl = services.issue_token(user)
    return token, ttl, AuthToken.objects.get()


def decode(token, **kw):
    return jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=["HS256"], issuer=settings.JWT_ISSUER, **kw)


def get_me(client, token):
    return client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")


def forge(claims, key=None, algorithm="HS256"):
    return jwt.encode(claims, key or settings.JWT_SIGNING_KEY, algorithm=algorithm)


def good_claims(user, session, **over):
    now = timezone.now()
    return {
        "iss": settings.JWT_ISSUER,
        "sub": str(user.pk),
        "jti": str(session.pk),
        "iat": now,
        "exp": now + timedelta(hours=1),
        **over,
    }


def test_token_is_a_signed_jwt_with_expected_claims(user, issued):
    token, ttl, session = issued
    assert token.count(".") == 2 and ttl == settings.AUTH_TOKEN_TTL_SECONDS
    claims = decode(token)
    assert claims["sub"] == str(user.pk) and claims["jti"] == str(session.pk)
    assert claims["iss"] == settings.JWT_ISSUER and claims["exp"] - claims["iat"] == ttl
    assert "email" not in claims and "name" not in claims  # no personal data in the token


def test_valid_jwt_authenticates(client, user, issued):
    assert get_me(client, issued[0]).status_code == 200


def test_tampered_payload_rejected(client, user, issued):
    head, payload, sig = issued[0].split(".")
    forged_payload = jwt.utils.base64url_encode(b'{"sub":"x"}').decode()
    assert get_me(client, f"{head}.{forged_payload}.{sig}").status_code == 401


def test_wrong_signing_key_rejected(client, user, issued):
    session = issued[2]
    assert (
        get_me(
            client, forge(good_claims(user, session), key="some-other-key-of-sufficient-length-123")
        ).status_code
        == 401
    )


def test_alg_none_rejected(client, user, issued):
    unsigned = jwt.encode(good_claims(user, issued[2]), key=None, algorithm="none")
    assert get_me(client, unsigned).status_code == 401


def test_expired_jwt_rejected_even_if_session_row_is_live(client, user, issued):
    session = issued[2]
    expired = forge(good_claims(user, session, exp=timezone.now() - timedelta(seconds=5)))
    assert get_me(client, expired).status_code == 401


def test_wrong_issuer_and_missing_claims_rejected(client, user, issued):
    session = issued[2]
    assert get_me(client, forge(good_claims(user, session, iss="someone-else"))).status_code == 401
    missing = good_claims(user, session)
    del missing["jti"]
    assert get_me(client, forge(missing)).status_code == 401


def test_session_row_expiry_revocation_and_deactivation_override_a_valid_signature(client, user, issued):
    token, _, session = issued
    AuthToken.objects.filter(pk=session.pk).update(revoked_at=timezone.now())
    assert get_me(client, token).status_code == 401
    AuthToken.objects.filter(pk=session.pk).update(
        revoked_at=None, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert get_me(client, token).status_code == 401
    AuthToken.objects.filter(pk=session.pk).update(expires_at=timezone.now() + timedelta(days=1))
    assert get_me(client, token).status_code == 200
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert get_me(client, token).status_code == 401


def test_jwt_for_another_users_session_is_rejected(client, user, issued):
    other = User.objects.create_user(email="o@x.com", name="O", mobile="9000099999")
    assert get_me(client, forge(good_claims(other, issued[2]))).status_code == 401  # sub does not own the jti


def test_unknown_session_and_garbage_rejected(client, user):
    ghost = {
        "iss": settings.JWT_ISSUER,
        "sub": str(user.pk),
        "jti": str(uuid.uuid4()),
        "iat": timezone.now(),
        "exp": timezone.now() + timedelta(hours=1),
    }
    assert get_me(client, forge(ghost)).status_code == 401
    for junk in ("abc", "a.b.c", "", "Bearer"):
        assert get_me(client, junk).status_code == 401


def test_logout_revokes_the_jwt(client, user, issued):
    token = issued[0]
    assert client.post("/api/v1/auth/logout", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 204
    assert get_me(client, token).status_code == 401


def test_each_login_gets_its_own_token_and_session(user):
    first, _ = services.issue_token(user)
    second, _ = services.issue_token(user)
    assert first != second and AuthToken.objects.count() == 2
