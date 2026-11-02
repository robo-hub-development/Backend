import re

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import User

SEND = "/api/v1/auth/otp/send"
VERIFY = "/api/v1/auth/otp/verify"


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="aarav@example.com", name="Aarav Mehta", mobile="9876543210")


def last_code() -> str:
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


def register(api, email="new@example.com"):
    api.post(
        SEND,
        {"purpose": "register", "email": email, "name": "New User", "mobile": "9876543210"},
        format="json",
    )
    return api.post(VERIFY, {"purpose": "register", "email": email, "otp": last_code()}, format="json")


def login_token(api, email):
    api.post(SEND, {"purpose": "login", "email": email}, format="json")
    return api.post(VERIFY, {"purpose": "login", "email": email, "otp": last_code()}, format="json").data[
        "token"
    ]
