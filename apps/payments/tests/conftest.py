import pytest
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def client_for(user) -> APIClient:
    client = APIClient()
    token, _ = services.issue_token(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def member(db):
    return User.objects.create_user(email="aarav@example.com", name="Aarav Mehta", mobile="9876543210")


@pytest.fixture
def other_member(db):
    return User.objects.create_user(email="isha@example.com", name="Isha Rao", mobile="9123456780")


@pytest.fixture
def api(member):
    return client_for(member)


@pytest.fixture(autouse=True)
def gateway():
    """A fresh in-memory gateway per test (settings.PAYMENT_GATEWAY is "fake" under test settings)."""
    from apps.payments.gateway import reset_fake_gateway

    return reset_fake_gateway()
